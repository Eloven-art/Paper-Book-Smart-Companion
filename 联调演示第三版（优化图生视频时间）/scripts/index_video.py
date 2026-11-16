"""Index an authorized or self-created book video. Calls the configured VLM."""
import argparse
import math
import os
import sys
import tempfile
import time
from pathlib import Path
from string import Template

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cv2
import requests
from agents.pipeline import build_pipeline
from agents.content_review import sha256
from agents.video_index import embed, track
from agents.story_agent import load_prompt
from server.errors import AppError, VLMFailed
from server.retry import call_with_retry, is_retryable
from agents.vlm_agent import encode_image, extract_json, normalize_vlm_result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("book")
    parser.add_argument("--step", type=float, default=0.5)
    parser.add_argument("--page", type=int, help="index only this page")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--anchor-delay", type=float, default=2.0, help="min seconds between VLM requests")
    args = parser.parse_args()
    if not 0.2 <= args.step <= 2:
        parser.error("--step must be in [0.2, 2]")
    pipe = build_pipeline()
    prompt = load_prompt(pipe.settings.prompts_dir,"video_frame_v1")
    book = pipe.store.load(args.book)
    video = book["video"]["path"]
    source = pipe.store.resolve_media(args.book, video)
    digest = sha256(source)
    if not pipe.vlm.status()["available"]:
        parser.error("Configure VLM_API_URL before indexing video.")
    capture = cv2.VideoCapture(str(source))
    failures = []
    indexed_count = 0
    last_call = 0.0
    metadata = {"sha256":digest,"step":args.step,"prompt_version":prompt.version,
                "prompt_sha256":prompt.sha256,"vlm_model":pipe.settings.vlm_model,"embedding_model":os.getenv("PBSC_EMBEDDINGS_MODEL")}
    try:
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "frame.jpg"
            for page in book["pages"]:
                bounds = page.get("time_range")
                if not bounds:
                    continue
                number = page["page_no"]
                if args.page and number != args.page:
                    continue
                if not args.force and page.get("video_index") == {**metadata,"complete":True}:
                    continue
                prior = page.get("video_index") or {}
                matches = all(prior.get(k) == v for k, v in metadata.items() if k != "embedding_model")
                frames = [dict(frame) for frame in page.get("keyframes") or []] if matches and not args.force else []
                if prior.get("embedding_model") != metadata["embedding_model"]:
                    for frame in frames:
                        frame.pop("embedding", None)
                        frame.pop("embedding_model", None)
                previous_count = len(frames)
                previous, objects, last_anchor = None, [], -100
                start, end = bounds
                if frames:
                    capture.set(cv2.CAP_PROP_POS_MSEC, frames[-1]["time"] * 1000)
                    ok, image_frame = capture.read()
                    if ok:
                        previous = cv2.cvtColor(image_frame, cv2.COLOR_BGR2GRAY)
                        objects = frames[-1]["objects"]
                        last_anchor = max((f["time"] for f in frames if f["method"] == "vlm"), default=-100)
                    else:
                        frames.clear()
                target_count = max(1, math.ceil((end-start)/args.step))
                for i in range(len(frames), target_count):
                    ts = min(start+i*args.step, (start+end)/2 if i == 0 else end-0.001)
                    capture.set(cv2.CAP_PROP_POS_MSEC, ts*1000)
                    ok, frame = capture.read()
                    if not ok:
                        failures.append((number, round(ts,2), "VIDEO_DECODE_FAILED"))
                        break
                    h, w = frame.shape[:2]
                    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                    cut = previous is not None and cv2.absdiff(previous, gray).mean() > 30
                    if ts-last_anchor >= 2 or cut:
                        image.write_bytes(cv2.imencode(".jpg", frame)[1].tobytes())
                        uri, sent = encode_image(image)
                        def describe():
                            nonlocal last_call
                            time.sleep(max(0, last_call + args.anchor_delay - time.monotonic()))
                            last_call = time.monotonic()
                            result, _ = pipe.vlm.analyze_image(uri,prompt.sections["system"],
                                Template(prompt.sections["user"]).substitute(page_no=number,width=sent[0],height=sent[1]),
                                page_no=number,image_size={"width":sent[0],"height":sent[1]})
                            return normalize_vlm_result(extract_json(result.text),expected_page_no=number,
                                                        sent_size=sent,original_size=(w,h))
                        try:
                            data = call_with_retry(describe, attempts=3, base_delay=6,
                                                   should_retry=lambda e: isinstance(e,VLMFailed) or is_retryable(e),
                                                   label=f"视频帧 {ts:.2f}s")
                        except AppError as exc:
                            failures.append((number,round(ts,2),exc.code))
                            # Resume at this frame on the next run.
                            break
                        objects = [{k:v for k,v in item.items() if k != "contours"} for item in data["objects"]]
                        summary = data["scene_summary"]
                        last_anchor = ts
                        method = "vlm"
                    else:
                        objects = track(previous, gray, objects)
                        summary, method = "", "tracking"
                    frames.append({"time": round(ts,3), "frame_size":{"width":w,"height":h},
                                   "objects":objects,"scene_summary":summary,"method":method,"prompt_version":prompt.version})
                    previous = gray
                complete = len(frames) == target_count
                anchors = [f for f in frames if f["scene_summary"] and not f.get("embedding")]
                try:
                    vectors = embed([f["scene_summary"] for f in anchors], ledger=pipe.ledger) if anchors else None
                except (ValueError,KeyError,requests.RequestException,AppError):
                    failures.append((number,None,"EMBEDDINGS_FAILED"))
                    complete = False
                    vectors = None
                if vectors:
                    for frame, vector in zip(anchors, vectors):
                        frame["embedding"] = vector
                        frame["embedding_model"] = os.getenv("PBSC_EMBEDDINGS_MODEL")
                def save(current):
                    if current.get("video") != book.get("video") or sha256(source) != digest:
                        raise RuntimeError("Video changed while indexing")
                    target = pipe.store.get_page(current, number)
                    target["keyframes"] = frames
                    target["video_index"] = {**metadata,"complete":complete}
                if frames:
                    pipe.store.update(args.book, save)
                indexed_count += max(0, len(frames)-previous_count)
    finally:
        capture.release()
    print(f"Indexed {indexed_count} frames this run")
    for number,second,code in failures:
        print(f"Page {number}, time {second}: {code}",file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
