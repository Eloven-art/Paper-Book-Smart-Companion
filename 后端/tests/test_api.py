# -*- coding: utf-8 -*-
"""接口测试：统一响应外壳、假接口（A 步骤 2）、真实接口的上传/生成/任务/追溯。"""
from __future__ import annotations

import io
import time

import pytest

from conftest import make_png_bytes, make_wav_bytes

MOCK = "/mock/api/v1"
API = "/api/v1"
ENVELOPE_KEYS = {"status", "data", "error", "run_id", "elapsed_ms", "mock"}


def body_of(resp) -> dict:
    data = resp.get_json()
    assert set(data) == ENVELOPE_KEYS, data
    assert (data["status"] == "ok") == (data["error"] is None)
    assert isinstance(data["elapsed_ms"], int) and data["run_id"]
    return data


def wait_task(client, prefix: str, task_id: str, timeout: float = 60.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        task = body_of(client.get(f"{prefix}/tasks/{task_id}"))["data"]
        if task["status"] in ("succeeded", "failed", "cancelled"):
            return task
        time.sleep(0.1)
    raise AssertionError(f"任务 {task_id} 超时未结束")


# ---------------------------------------------------------------- 外壳与错误
def test_envelope_on_success_and_errors(client):
    ok = body_of(client.get(f"{API}/health"))
    assert ok["mock"] is False and ok["data"]["degraded"] == ["llm", "tts", "i2v"]
    assert body_of(client.get(f"{MOCK}/health"))["mock"] is True

    for resp, status, code in [
        (client.get(f"{API}/no-such-endpoint"), 404, "NOT_FOUND"),
        (client.get(f"{MOCK}/no-such-endpoint"), 404, "NOT_FOUND"),
        (client.delete(f"{API}/books"), 405, "METHOD_NOT_ALLOWED"),
        (client.get(f"{API}/books/BAD ID"), 400, "INVALID_ARGUMENT"),
        (client.get(f"{API}/books/nope"), 404, "BOOK_NOT_FOUND"),
        (client.get(f"{API}/tasks/task-nope"), 404, "TASK_NOT_FOUND"),
    ]:
        assert resp.status_code == status
        assert body_of(resp)["error"]["code"] == code


def test_unfinished_real_endpoints_point_to_mock(client):
    resp = client.post(f"{API}/qa/ask", json={"book_id": "demo_book", "page_no": 1, "question": "?"})
    assert resp.status_code == 501
    error = body_of(resp)["error"]
    assert error["code"] == "NOT_IMPLEMENTED" and error["details"]["mock_path"] == f"{MOCK}/qa/ask"


def test_cors_headers_and_preflight(client):
    assert client.get(f"{API}/health").headers["Access-Control-Allow-Origin"] == "*"
    assert client.options(f"{MOCK}/qa/ask").status_code == 204


# ---------------------------------------------------------------- 假接口：绘本与分页数据
def test_mock_books_and_pages(client):
    books = body_of(client.get(f"{MOCK}/books"))["data"]["books"]
    assert books[0]["book_id"] == "demo_book" and books[0]["page_count"] == 3 and books[0]["video"]["ready"]

    book = body_of(client.get(f"{MOCK}/books/demo_book"))["data"]
    assert [c["name"] for c in book["characters"]] == ["朵朵", "乌龟爷爷"]
    assert len(book["video"]["clips"]) == 3 and book["video"]["clips"][0]["time_range"][0] == 0.0

    page = body_of(client.get(f"{MOCK}/books/demo_book/pages/2"))["data"]
    for field in ("book_id", "page_no", "page_image", "ocr_text", "narration", "characters", "objects",
                  "scene_summary", "plot_events", "video_clip_id", "time_range", "keyframes",
                  "source_reference", "safety_check"):                       # 共同约定 零.1 的全部字段
        assert field in page, field
    assert page["ocr_text"]["blocks"][0]["bbox"] and page["keyframes"][0]["objects"][0]["bbox"]
    assert page["safety_check"]["age_range"] == "3-6"

    for url in (page["page_image_url"], page["video_clip_url"], page["narration_audio_url"],
                book["video"]["url"]):
        media = client.get(url)
        assert media.status_code == 200 and len(media.data) > 1000
    assert client.get(f"{MOCK}/media/demo_book/../../../CLAUDE.md").status_code == 404
    assert body_of(client.get(f"{MOCK}/books/demo_book/pages/9"))["error"]["code"] == "PAGE_NOT_FOUND"
    assert body_of(client.get(f"{MOCK}/books/other_book"))["error"]["code"] == "BOOK_NOT_FOUND"


def test_mock_video_supports_seeking(client):
    url = body_of(client.get(f"{MOCK}/books/demo_book"))["data"]["video"]["url"]
    part = client.get(url, headers={"Range": "bytes=0-99"})
    assert part.status_code == 206 and len(part.data) == 100       # 浏览器拖动进度条需要分段请求


# ---------------------------------------------------------------- 假接口：问答、触控、历史
def test_mock_qa_grounded_and_ungrounded(client):
    payload = {"book_id": "demo_book", "page_no": 2, "play_time": 12.5, "session_id": "s1",
               "question": "月亮掉到哪里去了？"}
    turn = body_of(client.post(f"{MOCK}/qa/ask", json=payload))["data"]
    assert turn["grounded"] and "影子" in turn["answer_text"] and turn["play_time"] == 12.5
    page_ev, video_ev = turn["evidence"]
    assert page_ev["type"] == "page" and page_ev["page_no"] == 2 and page_ev["quote"]
    assert video_ev["type"] == "video" and video_ev["time_range"][0] <= video_ev["timestamp"] < video_ev["time_range"][1]
    assert set(turn["timings"]) == {"asr_ms", "retrieve_ms", "llm_ms", "tts_ms", "first_audio_ms"}

    miss = body_of(client.post(f"{MOCK}/qa/ask", json={**payload, "question": "恐龙吃什么？"}))["data"]
    assert miss["grounded"] is False and miss["evidence"] == [] and "没有提到" in miss["answer_text"]

    audio = {"book_id": "demo_book", "page_no": "1", "session_id": "s1",
             "audio": (io.BytesIO(make_wav_bytes()), "q.wav")}
    spoken = body_of(client.post(f"{MOCK}/qa/ask", data=audio, content_type="multipart/form-data"))["data"]
    assert spoken["question_audio"]["format"] == "wav" and spoken["question_audio_url"]

    history = body_of(client.get(f"{MOCK}/history?session_id=s1"))["data"]["turns"]
    assert len(history) == 3 and history[0]["question_text"] == "月亮掉到哪里去了？" and history[0]["snapshot_url"]

    for bad, field in [({"page_no": 1, "question": "x"}, "book_id"), ({"book_id": "demo_book", "question": "x"}, "page_no"),
                       ({"book_id": "demo_book", "page_no": 1}, "question"),
                       ({"book_id": "demo_book", "page_no": 0, "question": "x"}, "page_no")]:
        resp = client.post(f"{MOCK}/qa/ask", json=bad)
        assert resp.status_code == 400 and body_of(resp)["error"]["details"]["field"] == field


def test_mock_touch_uses_real_boxes(client, sample_dir):
    import json
    page1 = json.loads((sample_dir / "book.json").read_text(encoding="utf-8"))["pages"][0]
    boxes = {o["name"]: o["bbox"] for o in page1["objects"]}

    def touch(**kw):
        return body_of(client.post(f"{MOCK}/touch/identify", json={"book_id": "demo_book", "page_no": 1, **kw}))

    def center(name):
        x1, y1, x2, y2 = boxes[name]
        return {"x": (x1 + x2) / 2, "y": (y1 + y2) / 2}

    moon = touch(**center("月亮"))["data"]
    assert moon["hit"] and moon["object"]["name"] == "月亮" and moon["source"] == "page" and moon["object"]["explanation"]
    scarf = touch(**center("围巾"))["data"]                    # 围巾的框在朵朵的框里面：取面积最小的
    assert scarf["object"]["name"] == "围巾" and scarf["candidates"] == ["围巾", "朵朵"]
    assert touch(x=5, y=5)["data"] == {**touch(x=5, y=5)["data"], "hit": False, "object": None, "audio_url": None}

    x1, y1, x2, y2 = boxes["月亮"]
    norm = touch(x=(x1 + x2) / 2 / 1280, y=(y1 + y2) / 2 / 720, coord_space="normalized")["data"]
    assert norm["object"]["name"] == "月亮"

    paused = touch(**center("月亮"), play_time=15.0)["data"]     # 视频暂停在第 2 页：按最近关键帧查，不用原页图的框
    assert paused["source"] == "keyframe" and paused["page_no"] == 2 and paused["keyframe_time"] is not None

    out = client.post(f"{MOCK}/touch/identify", json={"book_id": "demo_book", "page_no": 1, "x": 5000, "y": 10})
    assert out.status_code == 400 and body_of(out)["error"]["code"] == "POINT_OUT_OF_RANGE"
    assert client.post(f"{MOCK}/touch/identify", json={"book_id": "demo_book", "page_no": 1}).status_code == 400


def test_mock_keyframe_boxes_follow_the_zoom(client, sample_dir):
    """示例视频里画面在缓慢推近，关键帧里的框应比原页图的框离中心更远一点。"""
    import json
    page = json.loads((sample_dir / "book.json").read_text(encoding="utf-8"))["pages"][0]
    original = next(o["bbox"] for o in page["objects"] if o["name"] == "月亮")
    later = next(o["bbox"] for o in page["keyframes"][1]["objects"] if o["name"] == "月亮")
    assert later[2] > original[2] and later[1] < original[1]


# ---------------------------------------------------------------- 假接口：任务、上传、调试开关
def test_mock_tasks_progress_to_done(client):
    task = body_of(client.post(f"{MOCK}/generate/narration", json={"book_id": "demo_book", "page_no": 1}))
    assert task["data"]["status"] == "queued" and task["data"]["progress"] == 0.0
    done = wait_task(client, MOCK, task["data"]["task_id"], timeout=10)
    assert done["status"] == "succeeded" and done["result"]["pages"][0]["narration"]

    video = body_of(client.post(f"{MOCK}/generate/video", json={"book_id": "demo_book"}))["data"]
    time.sleep(1.0)
    running = body_of(client.get(f"{MOCK}/tasks/{video['task_id']}"))["data"]
    assert running["status"] == "running" and 0 < running["progress"] < 1 and running["stage"] in ("tts", "i2v")

    failing = body_of(client.post(f"{MOCK}/generate/narration?mock_fail=1", json={"book_id": "demo_book"}))["data"]
    failed = wait_task(client, MOCK, failing["task_id"], timeout=10)
    assert failed["status"] == "failed" and failed["error"]["code"] == "MOCK_FAILURE"


def test_mock_debug_switches(client):
    resp = client.get(f"{MOCK}/books?mock_fail=1")
    assert resp.status_code == 500 and body_of(resp)["error"]["code"] == "MOCK_FAILURE"
    slow = body_of(client.get(f"{MOCK}/books?mock_delay_ms=300"))
    assert slow["elapsed_ms"] >= 300


def test_mock_upload_and_speech_validate_files(client):
    good = {"title": "我的绘本", "pages": [(io.BytesIO(make_png_bytes()), "1.png"), (io.BytesIO(make_png_bytes()), "2.png")]}
    resp = client.post(f"{MOCK}/books", data=good, content_type="multipart/form-data")
    data = body_of(resp)["data"]
    assert resp.status_code == 202 and len(data["accepted_pages"]) == 2 and data["task_id"]

    bad = {"pages": [(io.BytesIO(make_png_bytes()), "1.png"), (io.BytesIO(b"not an image"), "2.png")]}
    resp = client.post(f"{MOCK}/books", data=bad, content_type="multipart/form-data")
    error = body_of(resp)["error"]
    assert resp.status_code == 415 and error["code"] == "UPLOAD_CONTENT_MISMATCH" and error["details"]["field"] == "pages[2]"

    asr = client.post(f"{MOCK}/asr", data={"audio": (io.BytesIO(make_wav_bytes()), "q.wav")},
                      content_type="multipart/form-data")
    assert body_of(asr)["data"]["text"]
    assert client.post(f"{MOCK}/asr", data={}, content_type="multipart/form-data").status_code == 400
    assert body_of(client.post(f"{MOCK}/tts", json={"text": "你好"}))["data"]["audio_url"]


# ---------------------------------------------------------------- 真实接口：上传 → 旁白 → 视频 → 追溯
def test_real_upload_generate_and_trace(client, app):
    pages = [(io.BytesIO(make_png_bytes((640, 360), (200, 220, 250))), "封面.png"),
             (io.BytesIO(make_png_bytes((640, 360), (250, 230, 200))), "第二页.png")]
    resp = client.post(f"{API}/books", data={"title": "测试绘本", "book_id": "upload_test", "pages": pages},
                       content_type="multipart/form-data")
    created = body_of(resp)["data"]
    assert resp.status_code == 201 and created["book_id"] == "upload_test" and created["page_count"] == 2

    dup = client.post(f"{API}/books", data={"book_id": "upload_test", "pages": [(io.BytesIO(make_png_bytes()), "a.png")]},
                      content_type="multipart/form-data")
    assert dup.status_code == 409 and body_of(dup)["error"]["code"] == "BOOK_EXISTS"

    bad = client.post(f"{API}/books", data={"book_id": "half_book", "pages": [
        (io.BytesIO(make_png_bytes()), "ok.png"), (io.BytesIO(b"GIF89a" + b"0" * 50), "bad.png")]},
        content_type="multipart/form-data")
    assert bad.status_code == 415
    assert client.get(f"{API}/books/half_book").status_code == 404            # 有一页不合格就整本拒绝，不留半本书

    page = body_of(client.get(f"{API}/books/upload_test/pages/1"))["data"]
    assert page["page_image"] == "pages/page_001.png" and page["image_size"] == {"width": 640, "height": 360}
    assert page["ocr_text"] == {"full_text": "", "blocks": []} and page["safety_check"]["status"] == "pending_review"
    assert client.get(page["page_image_url"]).status_code == 200
    assert client.get(f"{API}/media/upload_test/../../../CLAUDE.md").status_code in (400, 404)

    # 内容理解（B 的模块）还没接入：这里手工填入第 1 页的原文，模拟 B 写入后的状态
    store = app.extensions["pbsc"]["pipeline"].store
    store.update("upload_test", lambda b: b["pages"][0].update(
        ocr_text={"full_text": "小熊醒来了，窗外下雪了。", "blocks": []}, scene_summary="小熊在窗边看雪"))

    task = body_of(client.post(f"{API}/generate/narration", json={"book_id": "upload_test"}))
    assert task["data"]["status"] in ("queued", "running")
    done = wait_task(client, API, task["data"]["task_id"])
    assert done["status"] == "succeeded" and done["result"]["pages"][0]["narration"] == "小熊醒来了，窗外下雪了。"
    assert done["result"]["pages"][0]["method"] == "passthrough_ocr" and done["run_id"]

    video_task = body_of(client.post(f"{API}/generate/video", json={"book_id": "upload_test"}))["data"]
    again = body_of(client.post(f"{API}/generate/video", json={"book_id": "upload_test"}))["data"]
    assert again["task_id"] == video_task["task_id"]                           # 重复提交返回同一个任务
    video_done = wait_task(client, API, video_task["task_id"])
    assert video_done["status"] == "succeeded", video_done
    result = video_done["result"]
    assert result["fallback_pages"] == [1, 2] and len(result["clips"]) == 2
    assert client.get(result["video_url"]).status_code == 200

    book = body_of(client.get(f"{API}/books/upload_test"))["data"]
    assert book["video"]["duration"] == result["duration"] and book["video"]["clips"][1]["clip_url"]
    page1 = body_of(client.get(f"{API}/books/upload_test/pages/1"))["data"]
    assert page1["time_range"] == result["clips"][0]["time_range"] and page1["video_clip_url"]

    run = body_of(client.get(f"{API}/runs/{video_done['run_id']}"))["data"]     # 凭 run_id 找回这次生成的来龙去脉
    assert run["kind"] == "video_book" and run["status"] == "ok" and run["input"]["book_id"] == "upload_test"
    assert body_of(client.get(f"{API}/runs/nope"))["error"]["code"] == "RUN_NOT_FOUND"

    costs = body_of(client.get(f"{API}/costs"))["data"]
    assert costs["total"]["i2v"]["calls"] == 2 and costs["total"]["i2v"]["billable_calls"] == 0
    listed = body_of(client.get(f"{API}/tasks?book_id=upload_test"))["data"]["tasks"]
    assert {t["kind"] for t in listed} == {"narration", "video"}
    books = body_of(client.get(f"{API}/books"))["data"]["books"]
    assert books[0]["book_id"] == "upload_test" and books[0]["video"]["ready"]


def test_real_generate_validates_target(client):
    assert client.post(f"{API}/generate/video", json={}).status_code == 400
    resp = client.post(f"{API}/generate/video", json={"book_id": "missing_book"})
    assert resp.status_code == 404 and body_of(resp)["error"]["code"] == "BOOK_NOT_FOUND"
    resp = client.post(f"{API}/books", data={}, content_type="multipart/form-data")
    assert resp.status_code == 400 and body_of(resp)["error"]["code"] == "UPLOAD_MISSING"


def test_unexpected_exception_is_not_leaked(app, client, monkeypatch):
    store = app.extensions["pbsc"]["pipeline"].store
    monkeypatch.setattr(store, "list_ids", lambda: (_ for _ in ()).throw(RuntimeError("secret path C:/x")))
    resp = client.get(f"{API}/books")
    error = body_of(resp)["error"]
    assert resp.status_code == 500 and error["code"] == "INTERNAL_ERROR" and "secret" not in error["message"]
