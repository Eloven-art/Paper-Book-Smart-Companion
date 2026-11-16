"""School-demo review workflow. Run locally as the designated team reviewer."""
import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agents.content_review import fingerprint, model_screen, review_prompt, read_record, review_path, timestamp
from agents.pipeline import build_pipeline
from agents.vlm_agent import encode_image


def screen(pipe, book):
    import cv2
    findings = []
    prompt = review_prompt()
    for page in book["pages"]:
        narration = page.get("narration") or ""
        if narration and not model_screen(pipe.qa.chat, {"narration":narration}):
            findings.append({"page_no":page["page_no"],"type":"text"})
    video = (book.get("video") or {}).get("path")
    ranges = []
    for page in book["pages"]:
        meta = page.get("video_meta") or {}
        if video and page.get("time_range"):
            ranges.append((page["page_no"],video,*page["time_range"]))
        elif meta.get("clip_path") and meta.get("clip_duration"):
            ranges.append((page["page_no"],meta["clip_path"],0,meta["clip_duration"]))
    with tempfile.TemporaryDirectory() as directory:
        image = Path(directory)/"frame.jpg"
        for page_no,path,start,end in ranges:
            cap = cv2.VideoCapture(str(pipe.store.resolve_media(book["book_id"],path)))
            try:
                for fraction in (0.15,0.5,0.85):
                    second = start+(end-start)*fraction
                    cap.set(cv2.CAP_PROP_POS_MSEC,second*1000)
                    try:
                        ok, frame = cap.read()
                        if not ok:
                            raise RuntimeError("Decode failed")
                        image.write_bytes(cv2.imencode(".jpg",frame)[1].tobytes())
                        uri, size = encode_image(image)
                        result, _ = pipe.vlm.analyze_image(
                            uri,prompt.sections["system"],prompt.sections["user"],
                            page_no=page_no,image_size={"width":size[0],"height":size[1]})
                        if result.text.strip() != "PASS":
                            findings.append({"page_no":page_no,"timestamp":second,"type":"frame"})
                    except Exception as exc:
                        findings.append({"page_no":page_no,"timestamp":second,"type":"screen_error",
                                         "error":type(exc).__name__})
            finally:
                cap.release()
    return findings


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("book")
    parser.add_argument("action",choices=["screen","approve","reject"])
    parser.add_argument("--reviewer")
    parser.add_argument("--ages")
    parser.add_argument("--notes")
    args=parser.parse_args()
    if args.action!="screen" and not all((args.reviewer,args.ages,args.notes)):
        parser.error("provide reviewer, applicable ages, and review notes")
    pipe=build_pipeline()
    book=pipe.store.load(args.book)
    revision=fingerprint(pipe.store,book)
    path=review_path(pipe.settings.data_dir,args.book)
    record=read_record(path)
    if record.get("fingerprint")!=revision:
        record={"fingerprint":revision,"audit":record.get("audit",[])}
    if args.action=="screen":
        record["screen"]={"at":timestamp(),"findings":screen(pipe,book)}
        record.pop("human",None)
        entry={"action":"screen",**record["screen"]}
    else:
        if "screen" not in record:
            parser.error("run model screening for this revision first")
        entry={"decision":args.action,"reviewer":args.reviewer,"ages":args.ages,
               "notes":args.notes,"at":timestamp()}
        record["human"]=entry
    if revision!=fingerprint(pipe.store,pipe.store.load(args.book)):
        parser.error("book changed during review")
    record["audit"].append({"fingerprint":revision,**entry})
    path.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent,delete=False,mode="w",encoding="utf-8") as output:
        json.dump(record,output,ensure_ascii=False,indent=2)
    os.replace(output.name,path)
    print(json.dumps(entry,ensure_ascii=False))


if __name__=="__main__":
    main()
