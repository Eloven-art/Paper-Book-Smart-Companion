# -*- coding: utf-8 -*-
"""假接口（A 步骤 2）：路径前缀 /mock/api/v1，返回固定示例数据，供前端联调和评测脚本开发。

- 字段、错误码、响应外壳与真实接口（/api/v1）完全一致，前端只需改一个地址前缀就能切到真实接口；
- 数据来自仓库自带的自制模拟绘本 data/samples/demo_book；
- 每个响应都带 "mock": true。假接口的结果只能用于开发联调，不能当作系统的生成结果演示或提交。

调试开关（放在查询串或 JSON 体里都可以）：
    mock_delay_ms=1500     人为延迟，最多 10000，用来调“等待中”状态
    mock_fail=1            让本次请求失败（或让提交的任务最终失败），用来调“失败”状态
"""
from __future__ import annotations

import json
import secrets
import threading
import time
from pathlib import Path
from typing import Optional

from flask import Blueprint, current_app, request, send_file

from . import touch
from .book_store import check_book_id, check_page_no, with_urls
from .envelope import begin_request, ok
from .errors import AppError, NotFoundError, ValidationError
from .run_logger import now_iso
from .uploads import validate_audio, validate_image

bp = Blueprint("mock_api", __name__, url_prefix="/mock/api/v1")

SAMPLE_BOOK = "demo_book"
UNGROUNDED_ANSWER = "这个问题书里没有提到哦。"

# 固定问答：问题里出现任一关键词就命中（按顺序匹配，先命中先用）
QA_BANK = [
    {"keywords": ["围巾", "颜色"], "answer": "朵朵围着一条蓝色的围巾。", "page_no": 1,
     "quote": "朵朵，一只白色的小兔子，脖子上围着蓝色的围巾"},
    {"keywords": ["谁告诉", "谁说", "乌龟"], "answer": "是乌龟爷爷告诉朵朵的，那是月亮的影子。", "page_no": 2,
     "quote": "乌龟爷爷慢慢地说：“那是月亮的影子。”"},
    {"keywords": ["掉", "水里", "小河", "影子"], "answer": "月亮没有真的掉进水里，朵朵看到的是月亮在小河里的影子。",
     "page_no": 2, "quote": "朵朵走到小河边，发现月亮掉进了水里。"},
    {"keywords": ["最后", "山坡", "一起"], "answer": "最后，朵朵和乌龟爷爷一起坐在山坡上看月亮。", "page_no": 3,
     "quote": "朵朵和乌龟爷爷一起坐在山坡上看月亮。"},
    {"keywords": ["看见", "看到", "抬头"], "answer": "朵朵抬头看见了圆圆的月亮。", "page_no": 1,
     "quote": "小兔子朵朵抬头看见了圆圆的月亮。"},
    {"keywords": ["想", "住在哪"], "answer": "朵朵在想：月亮住在哪里呢？", "page_no": 1,
     "quote": "“月亮住在哪里呢？”朵朵想。"},
]

TASK_STAGES = {
    "ingest": (4.0, [(0.4, "ocr", "文字识别"), (1.0, "vlm", "画面理解")]),
    "narration": (2.0, [(1.0, "llm", "生成旁白")]),
    "video": (8.0, [(0.15, "tts", "旁白配音"), (0.75, "i2v", "生成视频片段"), (1.0, "compose", "合成配音、字幕并拼接")]),
}

_lock = threading.Lock()
_tasks: dict[str, dict] = {}
_history: dict[str, list] = {}


# ---------------------------------------------------------------- 工具
def _sample_dir() -> Path:
    return current_app.extensions["pbsc"]["settings"].samples_dir / SAMPLE_BOOK


def _load_book(book_id: str) -> dict:
    check_book_id(book_id)
    if book_id != SAMPLE_BOOK:
        raise NotFoundError(f"找不到绘本 {book_id}（假接口只有示例绘本 {SAMPLE_BOOK}）", code="BOOK_NOT_FOUND",
                            details={"book_id": book_id})
    return json.loads((_sample_dir() / "book.json").read_text(encoding="utf-8"))


def _page(book: dict, page_no: int) -> dict:
    for page in book["pages"]:
        if page["page_no"] == page_no:
            return page
    raise NotFoundError(f"绘本 {book['book_id']} 没有第 {page_no} 页", code="PAGE_NOT_FOUND",
                        details={"book_id": book["book_id"], "page_no": page_no})


def _media_url(relative: str) -> str:
    return f"/mock/api/v1/media/{SAMPLE_BOOK}/{relative}"


def _args() -> dict:
    """合并查询串、表单和 JSON 体里的参数。"""
    merged = dict(request.args.items())
    merged.update(request.form.items())
    body = request.get_json(silent=True)
    if isinstance(body, dict):
        merged.update(body)
    return merged


def _require(args: dict, name: str):
    value = args.get(name)
    if value is None or value == "":
        raise ValidationError(f"缺少参数 {name}", details={"field": name})
    return value


def _optional_float(args: dict, name: str) -> Optional[float]:
    value = args.get(name)
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValidationError(f"{name} 应为数字", details={"field": name}) from None


def _truthy(value) -> bool:
    return str(value).lower() in ("1", "true", "yes", "on")


@bp.before_request
def _before():
    begin_request(mock=True)
    args = _args()
    delay = _optional_float(args, "mock_delay_ms")
    if delay:
        time.sleep(min(10000.0, max(0.0, delay)) / 1000.0)
    # 提交任务的接口把 mock_fail 解释成“任务最终失败”，其余接口直接返回失败
    if _truthy(args.get("mock_fail", "")) and not request.path.startswith("/mock/api/v1/generate/"):
        raise AppError("这是假接口按 mock_fail 参数模拟的失败", code="MOCK_FAILURE", http_status=500)


# ---------------------------------------------------------------- 绘本
@bp.get("/health")
def health():
    return ok({"service": "paper-book-smart-companion", "mode": "mock", "sample_book": SAMPLE_BOOK})


def _summary(book: dict) -> dict:
    video = book.get("video") or {}
    return {"book_id": book["book_id"], "title": book["title"], "page_count": len(book["pages"]),
            "age_range": book.get("age_range"), "cover_image_url": _media_url(book["pages"][0]["page_image"]),
            "has_narration": True,
            "video": {"ready": bool(video.get("path")), "url": _media_url(video["path"]) if video.get("path") else None,
                      "duration": video.get("duration")}}


@bp.get("/books")
def list_books():
    return ok({"books": [_summary(_load_book(SAMPLE_BOOK))]})


@bp.post("/books")
def upload_book():
    """上传绘本。文件会被真实校验（类型、大小、能否解码），但不保存；返回示例绘本和一个模拟的入库任务。"""
    settings = current_app.extensions["pbsc"]["settings"]
    files = request.files.getlist("pages")
    if not files:
        raise ValidationError("请用 pages 字段上传至少一张页面图片", code="UPLOAD_MISSING", details={"field": "pages"})
    if len(files) > settings.max_pages_per_upload:
        raise ValidationError(f"一次最多上传 {settings.max_pages_per_upload} 页", code="UPLOAD_TOO_MANY")
    accepted = []
    for index, file in enumerate(files, start=1):
        upload = validate_image(file, max_bytes=int(settings.max_upload_mb * 1024 * 1024),
                                max_pixels=settings.max_image_pixels, field=f"pages[{index}]")
        accepted.append({"page_no": index, "filename": upload.original_name, "bytes": upload.size,
                         "width": upload.width, "height": upload.height})
    task = _new_task("ingest", SAMPLE_BOOK, None, {"book_id": SAMPLE_BOOK, "page_count": len(accepted)})
    return ok({"book_id": SAMPLE_BOOK, "title": request.form.get("title") or "示例绘本", "accepted_pages": accepted,
               "task_id": task["task_id"], "note": "假接口不保存上传的文件，后续请用返回的示例绘本 ID"}, 202)


@bp.get("/books/<book_id>")
def get_book(book_id: str):
    book = _load_book(book_id)
    video = dict(book.get("video") or {})
    if video.get("path"):
        video["url"] = _media_url(video["path"])
        video["clips"] = [{**c, "clip_url": _media_url(c["clip_path"])} for c in video.get("clips", [])]
    data = {k: book[k] for k in ("schema_version", "book_id", "title", "language", "age_range",
                                 "source_reference", "characters")}
    data.update(page_count=len(book["pages"]), video=video or None)
    return ok(data)


@bp.get("/books/<book_id>/pages")
def list_pages(book_id: str):
    book = _load_book(book_id)
    return ok({"book_id": book_id, "pages": [with_urls(p, _media_url) for p in book["pages"]]})


@bp.get("/books/<book_id>/pages/<page_no>")
def get_page(book_id: str, page_no: str):
    book = _load_book(book_id)
    return ok(with_urls(_page(book, check_page_no(page_no)), _media_url))


@bp.get("/media/<book_id>/<path:relative>")
def media(book_id: str, relative: str):
    check_book_id(book_id)
    base = (_sample_dir()).resolve()
    target = (base / relative).resolve()
    if book_id != SAMPLE_BOOK or (base != target and base not in target.parents) or not target.is_file():
        raise NotFoundError("文件不存在", code="FILE_NOT_FOUND", details={"path": relative})
    return send_file(target, conditional=True)


# ---------------------------------------------------------------- 任务
def _new_task(kind: str, book_id: str, page_no: Optional[int], result: dict, fail: bool = False) -> dict:
    task_id = f"mocktask-{time.strftime('%Y%m%dT%H%M%S')}-{secrets.token_hex(3)}"
    task = {"task_id": task_id, "kind": kind, "book_id": book_id, "page_no": page_no, "_created": time.time(),
            "created_at": now_iso(), "_result": result, "_fail": fail}
    with _lock:
        _tasks[task_id] = task
    return _task_view(task)


def _task_view(task: dict) -> dict:
    duration, stages = TASK_STAGES[task["kind"]]
    elapsed = time.time() - task["_created"]
    fraction = min(1.0, elapsed / duration)
    view = {"task_id": task["task_id"], "kind": task["kind"], "book_id": task["book_id"],
            "page_no": task["page_no"], "created_at": task["created_at"], "progress": round(fraction, 2),
            "stage": None, "message": "排队中", "status": "queued", "result": None, "error": None,
            "run_id": None}
    if elapsed < 0.3:
        return view
    for upper, stage, label in stages:
        if fraction <= upper:
            view.update(stage=stage, message=label, status="running")
            break
    if task["_fail"] and fraction >= 0.5:
        view.update(status="failed", progress=0.5, message="模拟失败",
                    error={"code": "MOCK_FAILURE", "message": "这是假接口按 mock_fail 参数模拟的任务失败"})
    elif fraction >= 1.0:
        view.update(status="succeeded", message="完成", result=task["_result"],
                    run_id=f"mock-{task['task_id'][9:]}")
    return view


@bp.post("/generate/narration")
def generate_narration():
    args = _args()
    book = _load_book(_require(args, "book_id"))
    page_no = check_page_no(args["page_no"]) if args.get("page_no") not in (None, "") else None
    pages = [_page(book, page_no)] if page_no else book["pages"]
    result = {"book_id": book["book_id"],
              "pages": [{"page_no": p["page_no"], "narration": p["narration"], "method": "mock",
                         "needs_review": False} for p in pages]}
    return ok(_new_task("narration", book["book_id"], page_no, result, _truthy(args.get("mock_fail", ""))), 202)


@bp.post("/generate/video")
def generate_video():
    args = _args()
    book = _load_book(_require(args, "book_id"))
    page_no = check_page_no(args["page_no"]) if args.get("page_no") not in (None, "") else None
    video = book.get("video") or {}
    if page_no:
        page = _page(book, page_no)
        meta = page.get("video_meta") or {}
        result = {"book_id": book["book_id"], "page_no": page_no, "video_clip_id": page.get("video_clip_id"),
                  "clip_url": _media_url(meta["clip_path"]) if meta.get("clip_path") else None,
                  "duration": meta.get("clip_duration")}
    else:
        result = {"book_id": book["book_id"], "video_url": _media_url(video["path"]) if video.get("path") else None,
                  "duration": video.get("duration"),
                  "clips": [{"video_clip_id": c["video_clip_id"], "page_no": c["page_no"],
                             "time_range": c["time_range"]} for c in video.get("clips", [])]}
    return ok(_new_task("video", book["book_id"], page_no, result, _truthy(args.get("mock_fail", ""))), 202)


@bp.get("/tasks/<task_id>")
def get_task(task_id: str):
    with _lock:
        task = _tasks.get(task_id)
    if not task:
        raise NotFoundError(f"找不到任务 {task_id}", code="TASK_NOT_FOUND")
    return ok(_task_view(task))


# ---------------------------------------------------------------- 语音
@bp.post("/asr")
def asr():
    settings = current_app.extensions["pbsc"]["settings"]
    upload = validate_audio(request.files.get("audio"), max_bytes=int(settings.max_upload_mb * 1024 * 1024))
    return ok({"text": "月亮掉到哪里去了？", "audio_bytes": upload.size, "audio_format": upload.format,
               "timings": {"asr_ms": 180}})


@bp.post("/tts")
def tts():
    args = _args()
    text = str(_require(args, "text"))
    return ok({"text": text, "audio_url": _media_url("audio/page_001.wav"), "voice": args.get("voice", "xiaosi"),
               "is_placeholder": True, "timings": {"tts_ms": 220}})


# ---------------------------------------------------------------- 问答
@bp.post("/qa/ask")
def qa_ask():
    """语音或文字提问。上传 audio 时先走（模拟的）语音识别；回答附带依据页码和视频时间点。"""
    args = _args()
    book = _load_book(_require(args, "book_id"))
    page_no = check_page_no(_require(args, "page_no"))
    _page(book, page_no)
    play_time = _optional_float(args, "play_time")
    session_id = str(args.get("session_id") or f"session-{secrets.token_hex(4)}")

    question_audio = None
    if request.files.get("audio"):
        settings = current_app.extensions["pbsc"]["settings"]
        upload = validate_audio(request.files["audio"], max_bytes=int(settings.max_upload_mb * 1024 * 1024))
        question = str(args.get("question") or "月亮掉到哪里去了？")
        question_audio = {"bytes": upload.size, "format": upload.format}
    else:
        question = str(_require(args, "question")).strip()
        if not question:
            raise ValidationError("question 不能为空", details={"field": "question"})

    match = next((qa for qa in QA_BANK if any(k in question for k in qa["keywords"])), None)
    if match:
        page = _page(book, match["page_no"])
        start, end = page["time_range"]
        evidence = [
            {"type": "page", "page_no": page["page_no"], "quote": match["quote"],
             "page_image_url": _media_url(page["page_image"])},
            {"type": "video", "page_no": page["page_no"], "video_clip_id": page["video_clip_id"],
             "time_range": [start, end], "timestamp": round(start + min(1.5, (end - start) / 4), 2),
             "video_url": _media_url(book["video"]["path"])},
        ]
        answer, grounded = match["answer"], True
    else:
        answer, grounded, evidence = UNGROUNDED_ANSWER, False, []

    turn = {
        "turn_id": f"turn-{secrets.token_hex(4)}", "session_id": session_id, "created_at": now_iso(),
        "book_id": book["book_id"], "page_no": page_no, "play_time": play_time,
        "question_text": question, "question_audio": question_audio,
        "question_audio_url": _media_url("audio/page_002.wav") if question_audio else None,
        "answer_text": answer, "answer_audio_url": _media_url(f"audio/page_{(match or {'page_no': 1})['page_no']:03d}.wav"),
        "grounded": grounded, "evidence": evidence,
        "snapshot_url": _media_url(_page(book, page_no)["page_image"]),
        "timings": {"asr_ms": 180 if question_audio else 0, "retrieve_ms": 35, "llm_ms": 420, "tts_ms": 210,
                    "first_audio_ms": 845 if question_audio else 665},
    }
    with _lock:
        _history.setdefault(session_id, []).append(turn)
    return ok(turn)


@bp.get("/history")
def history():
    """多模态对话历史：文字、提问录音、回答音频、当时的画面截图。只保存在内存里，服务重启即清空。"""
    args = _args()
    session_id = str(_require(args, "session_id"))
    with _lock:
        turns = list(_history.get(session_id, []))
    return ok({"session_id": session_id, "turns": turns})


# ---------------------------------------------------------------- 触控识别
@bp.post("/touch/identify")
def touch_identify():
    """点击识物：按坐标查物体框。带 play_time 时按最近的视频关键帧查，否则按原页图查。"""
    args = _args()
    book = _load_book(_require(args, "book_id"))
    page_no = check_page_no(_require(args, "page_no"))
    x, y = _optional_float(args, "x"), _optional_float(args, "y")
    if x is None or y is None:
        raise ValidationError("缺少点击坐标 x、y", details={"field": "x,y"})
    result = touch.identify(book, _page(book, page_no), x, y, play_time=_optional_float(args, "play_time"),
                            coord_space=str(args.get("coord_space", "image")))
    result["audio_url"] = _media_url(f"audio/page_{result['page_no']:03d}.wav") if result["hit"] else None
    return ok(result)
