# -*- coding: utf-8 -*-
"""真实接口：路径前缀 /api/v1。字段与假接口（/mock/api/v1）一致。

已接入真实实现：绘本上传与读取、旁白生成、视频生成、任务状态、运行记录与费用查询。
尚未接入（返回 501 NOT_IMPLEMENTED，可先用假接口）：语音识别、语音合成、问答、触控识别、对话历史
——它们依赖 B 的检索与识别模块和 A 后续的语音链路（步骤 6 以后）。
"""
from __future__ import annotations

import time
from typing import Optional

from flask import Blueprint, current_app, request, send_file

from .book_store import BookStore, check_book_id, check_page_no, empty_page, summarize_book, with_urls
from .envelope import begin_request, ok
from .errors import NotFoundError, NotImplementedYet, ValidationError
from .task_queue import TaskContext, public_task
from .uploads import validate_image

bp = Blueprint("api", __name__, url_prefix="/api/v1")


def _ext() -> dict:
    return current_app.extensions["pbsc"]


def _store() -> BookStore:
    return _ext()["pipeline"].store


def _media_url_for(book_id: str):
    return lambda relative: f"/api/v1/media/{book_id}/{relative}"


def _body() -> dict:
    merged = dict(request.args.items())
    merged.update(request.form.items())
    body = request.get_json(silent=True)
    if isinstance(body, dict):
        merged.update(body)
    return merged


def _flag(value) -> bool:
    return value is True or str(value).lower() in ("1", "true", "yes", "on")


@bp.before_request
def _before():
    begin_request(mock=False)


# ---------------------------------------------------------------- 系统
@bp.get("/health")
def health():
    ext = _ext()
    summary = ext["settings"].public_summary()
    return ok({"service": "paper-book-smart-companion", "mode": "real",
               "services": summary["services"], "limits": summary["limits"], "video": summary["video"],
               "degraded": [name for name, s in summary["services"].items() if not s["configured"]],
               "books": len(_store().list_ids())})


@bp.get("/costs")
def costs():
    ledger = _ext()["pipeline"].ledger
    return ok({"today": ledger.summary(time.strftime("%Y-%m-%d")), "total": ledger.summary(),
               "budgets": ledger.pricing.get("budgets", {}), "currency": ledger.pricing.get("currency")})


@bp.get("/runs/<run_id>")
def get_run(run_id: str):
    """按 run_id 找回一次生成的完整记录：输入、提示词版本、模型及参数、输出文件。"""
    if not run_id.replace("-", "").replace("_", "").isalnum():
        raise ValidationError("run_id 格式不正确")
    record = _ext()["pipeline"].runs.find(run_id)
    if not record:
        raise NotFoundError(f"找不到运行记录 {run_id}", code="RUN_NOT_FOUND")
    return ok(record)


# ---------------------------------------------------------------- 绘本
@bp.get("/books")
def list_books():
    store = _store()
    books = [summarize_book(store.load(book_id), _media_url_for(book_id)) for book_id in store.list_ids()]
    return ok({"books": books})


@bp.post("/books")
def upload_book():
    """上传一本绘本的页面图片（multipart，字段 pages 可重复，按上传顺序编页码）。

    全部文件先校验，任何一个不合格就整体拒绝，不会留下半本书。
    页面保存后，如果 VLM 已配置，会自动排一个 understanding 任务做画面理解
    （OCR、物体、摘要等）；没配置就把这些字段留空，返回的 task_id 为 null。
    """
    settings = _ext()["settings"]
    files = request.files.getlist("pages")
    if not files:
        raise ValidationError("请用 pages 字段上传至少一张页面图片", code="UPLOAD_MISSING", details={"field": "pages"})
    if len(files) > settings.max_pages_per_upload:
        raise ValidationError(f"一次最多上传 {settings.max_pages_per_upload} 页", code="UPLOAD_TOO_MANY")
    uploads = [validate_image(f, max_bytes=int(settings.max_upload_mb * 1024 * 1024),
                              max_pixels=settings.max_image_pixels, field=f"pages[{i}]")
               for i, f in enumerate(files, start=1)]
    store = _store()
    book_id = request.form.get("book_id") or None
    title = (request.form.get("title") or "").strip()
    book = store.create(title, book_id)
    pages_dir = store.book_dir(book["book_id"]) / "pages"
    for index, upload in enumerate(uploads, start=1):
        path = upload.save(pages_dir, f"page_{index:03d}")
        page = empty_page(book["book_id"], index, f"pages/{path.name}", upload.width, upload.height)
        page["source_reference"].update(original_filename=upload.original_name, sha256=upload.sha256)
        book["pages"].append(page)
    store.save(book)

    task = None
    if settings.vlm.configured:
        pipeline, tasks = _ext()["pipeline"], _ext()["tasks"]

        def job(ctx: TaskContext) -> dict:
            return pipeline.understanding.understand_book(book["book_id"], progress=ctx.progress,
                                                         check_cancelled=ctx.check_cancelled)

        task = tasks.submit("understanding", job, params={"book_id": book["book_id"]},
                            book_id=book["book_id"],
                            dedupe_key=f"understanding:{book['book_id']}:all")

    return ok({"book_id": book["book_id"], "title": book["title"], "page_count": len(uploads),
               "accepted_pages": [{"page_no": i, "filename": u.original_name, "bytes": u.size,
                                   "width": u.width, "height": u.height} for i, u in enumerate(uploads, 1)],
               "task_id": task["task_id"] if task else None,
               "note": "页面已保存。画面理解已排入队列，轮询 GET /tasks/{task_id} 看进度"
                       if task else "页面已保存。VLM 未配置，画面理解字段留空"}, 201)


@bp.post("/books/<book_id>/understand")
def understand_book(book_id: str):
    """对已存在的绘本重跑画面理解。可带 page_no 只跑一页，force=true 覆盖已有结果。"""
    book_id = check_book_id(book_id)
    book = _store().load(book_id)
    body = _body()
    page_no = check_page_no(body["page_no"]) if body.get("page_no") not in (None, "") else None
    if page_no is not None:
        BookStore.get_page(book, page_no)
    pipeline, tasks = _ext()["pipeline"], _ext()["tasks"]
    if not pipeline.understanding.configured:
        _pending("画面理解", "/mock/api/v1/books/demo_book/pages/1")
    force = _flag(body.get("force", False))

    def job(ctx: TaskContext) -> dict:
        return pipeline.understanding.understand_book(
            book_id, pages=[page_no] if page_no else None, force=force,
            progress=ctx.progress, check_cancelled=ctx.check_cancelled)

    task = tasks.submit("understanding", job,
                        params={"book_id": book_id, "page_no": page_no, "force": force},
                        book_id=book_id, page_no=page_no,
                        dedupe_key=f"understanding:{book_id}:{page_no or 'all'}")
    return ok(public_task(task), 202)


@bp.get("/books/<book_id>")
def get_book(book_id: str):
    book = _store().load(check_book_id(book_id))
    url = _media_url_for(book_id)
    video = dict(book.get("video") or {})
    if video.get("path"):
        video["url"] = url(video["path"])
        video["clips"] = [{**c, "clip_url": url(c["clip_path"])} for c in video.get("clips", [])]
    data = {k: book.get(k) for k in ("schema_version", "book_id", "title", "language", "age_range",
                                     "source_reference", "characters")}
    data.update(page_count=len(book.get("pages", [])), video=video or None)
    return ok(data)


@bp.get("/books/<book_id>/pages")
def list_pages(book_id: str):
    book = _store().load(check_book_id(book_id))
    return ok({"book_id": book_id, "pages": [with_urls(p, _media_url_for(book_id)) for p in book.get("pages", [])]})


@bp.get("/books/<book_id>/pages/<page_no>")
def get_page(book_id: str, page_no: str):
    book = _store().load(check_book_id(book_id))
    return ok(with_urls(BookStore.get_page(book, check_page_no(page_no)), _media_url_for(book_id)))


@bp.get("/media/<book_id>/<path:relative>")
def media(book_id: str, relative: str):
    return send_file(_store().resolve_media(check_book_id(book_id), relative), conditional=True)


# ---------------------------------------------------------------- 生成
def _target(body: dict) -> tuple[str, Optional[int]]:
    book_id = check_book_id(body.get("book_id"))
    page_no = check_page_no(body["page_no"]) if body.get("page_no") not in (None, "") else None
    book = _store().load(book_id)
    if page_no is not None:
        BookStore.get_page(book, page_no)
    return book_id, page_no


@bp.post("/generate/narration")
def generate_narration():
    """提交旁白生成任务。page_no 省略表示整本。立即返回 task_id。"""
    body = _body()
    book_id, page_no = _target(body)
    pipeline, tasks = _ext()["pipeline"], _ext()["tasks"]

    def job(ctx: TaskContext) -> dict:
        return pipeline.narration.generate_book(book_id, [page_no] if page_no else None,
                                                progress=ctx.progress, check_cancelled=ctx.check_cancelled)

    task = tasks.submit("narration", job, params={"book_id": book_id, "page_no": page_no}, book_id=book_id,
                        page_no=page_no, dedupe_key=f"narration:{book_id}:{page_no}")
    return ok(public_task(task), 202)


@bp.post("/generate/video")
def generate_video():
    """提交视频生成任务。带 page_no 只生成该页成片；省略则生成整本并写回各页 time_range。

    force=true 时忽略缓存重新调用收费服务。同一本书同一页的任务未结束前重复提交，返回的是同一个任务。
    """
    body = _body()
    book_id, page_no = _target(body)
    force = _flag(body.get("force", False))
    pipeline, tasks = _ext()["pipeline"], _ext()["tasks"]

    def job(ctx: TaskContext) -> dict:
        url = _media_url_for(book_id)
        if page_no:
            ctx.progress(0.05, f"第 {page_no} 页")
            clip = pipeline.video.generate_page_clip(book_id, page_no, force,
                                                     notify=lambda m: ctx.progress(0.5, m))
            return {"run_id": clip["run_id"], "book_id": book_id, "page_no": page_no,
                    "video_clip_id": clip["video_clip_id"], "clip_url": url(clip["clip_path"]),
                    "duration": clip["duration"], "provider": clip["provider"], "is_fallback": clip["is_fallback"],
                    "audio_placeholder": clip["audio_placeholder"]}
        result = pipeline.video.generate_book_video(book_id, force, progress=ctx.progress,
                                                    check_cancelled=ctx.check_cancelled)
        result["video_url"] = url(result["video_path"])
        return result

    task = tasks.submit("video", job, params={"book_id": book_id, "page_no": page_no, "force": force},
                        book_id=book_id, page_no=page_no, dedupe_key=f"video:{book_id}:{page_no}")
    return ok(public_task(task), 202)


# ---------------------------------------------------------------- 任务
@bp.get("/tasks/<task_id>")
def get_task(task_id: str):
    task = _ext()["tasks"].get(task_id)
    if not task:
        raise NotFoundError(f"找不到任务 {task_id}", code="TASK_NOT_FOUND")
    return ok(public_task(task))


@bp.get("/tasks")
def list_tasks():
    args = request.args
    tasks = _ext()["tasks"].list(book_id=args.get("book_id"), kind=args.get("kind"), status=args.get("status"))
    return ok({"tasks": [public_task(t) for t in tasks]})


@bp.post("/tasks/<task_id>/cancel")
def cancel_task(task_id: str):
    task = _ext()["tasks"].cancel(task_id)
    if not task:
        raise NotFoundError(f"找不到任务 {task_id}", code="TASK_NOT_FOUND")
    return ok(public_task(task))


# ---------------------------------------------------------------- 尚未接入
def _pending(feature: str, mock_path: str):
    raise NotImplementedYet(f"{feature}的真实实现尚未接入，联调请先用假接口 {mock_path}",
                            details={"mock_path": mock_path})


@bp.post("/asr")
def asr():
    _pending("语音识别", "/mock/api/v1/asr")


@bp.post("/tts")
def tts():
    _pending("语音合成", "/mock/api/v1/tts")


@bp.post("/qa/ask")
def qa_ask():
    """提问。文字走 question 字段（语音上传等 ASR 接进来再支持）。

    回答依据本页的 ocr_text / scene_summary / plot_events / objects 和相邻页原文；
    首选用文本大模型，没配置时退回 VLM 看图回答。grounded=false 表示书里找不到依据。
    """
    body = _body()
    book_id = check_book_id(body.get("book_id"))
    page_no = check_page_no(body.get("page_no"))
    question = str(body.get("question") or "").strip()
    if not question:
        raise ValidationError("question 不能为空", details={"field": "question"})
    if request.files.get("audio"):
        _pending("语音识别", "/mock/api/v1/asr")
    book = _store().load(book_id)
    BookStore.get_page(book, page_no)
    play_time = body.get("play_time")
    try:
        play_time = float(play_time) if play_time not in (None, "") else None
    except (TypeError, ValueError):
        raise ValidationError("play_time 应为秒数", details={"field": "play_time"}) from None
    pipeline = _ext()["pipeline"]
    turn = pipeline.qa.ask(book_id, page_no, question, play_time=play_time,
                           session_id=body.get("session_id"))
    return ok(turn)


def _bbox_hit(bbox, x: float, y: float) -> bool:
    return bool(bbox) and len(bbox) == 4 and bbox[0] <= x <= bbox[2] and bbox[1] <= y <= bbox[3]


def _bbox_area(bbox) -> float:
    return max(0.0, float(bbox[2]) - float(bbox[0])) * max(0.0, float(bbox[3]) - float(bbox[1]))


def _nearest_keyframe(page: dict, play_time: float) -> Optional[dict]:
    """视频暂停时按时间找最近的关键帧。没有关键帧数据就返回 None。"""
    best, best_gap = None, None
    for frame in page.get("keyframes") or []:
        try:
            gap = abs(float(frame.get("time")) - play_time)
        except (TypeError, ValueError):
            continue
        if best_gap is None or gap < best_gap:
            best, best_gap = frame, gap
    return best


@bp.post("/touch/identify")
def touch_identify():
    """点击识物：在画面理解写好的 objects 里查框，毫秒级返回，不再调用模型。

    静态页按页图的 objects 查；带 play_time 时优先用最近关键帧的对象表
    （视频里画面会动，不能把原页图的框直接套上去）；没有关键帧就退回页图对象，
    并在 source 字段里标明用的是哪一种。
    """
    started = time.perf_counter()
    body = _body()
    book_id = check_book_id(body.get("book_id"))
    page_no = check_page_no(body.get("page_no"))
    page = BookStore.get_page(_store().load(book_id), page_no)

    size = page.get("image_size") or {}
    width, height = int(size.get("width") or 0), int(size.get("height") or 0)
    if width <= 0 or height <= 0:
        raise ValidationError("这一页没有画面尺寸，无法按坐标识物",
                              code="PAGE_IMAGE_MISSING", details={"book_id": book_id, "page_no": page_no})
    try:
        x, y = float(body["x"]), float(body["y"])
    except (KeyError, TypeError, ValueError):
        raise ValidationError("x、y 必须是数字", details={"field": "x/y"}) from None
    if str(body.get("coord_space") or "image") == "normalized":
        x, y = x * width, y * height
    if not (0 <= x <= width and 0 <= y <= height):
        raise ValidationError(f"点击坐标 ({x:.0f}, {y:.0f}) 超出画面 {width}x{height}",
                              code="POINT_OUT_OF_RANGE",
                              details={"book_id": book_id, "page_no": page_no, "point": {"x": x, "y": y}})

    source, targets = "page", page.get("objects") or []
    frame_size = {"width": width, "height": height}
    keyframe_time = None
    play_time = body.get("play_time")
    if play_time not in (None, ""):
        try:
            frame = _nearest_keyframe(page, float(play_time))
        except (TypeError, ValueError):
            frame = None
        if frame and frame.get("objects"):
            source, targets = "keyframe", frame["objects"]
            keyframe_time = frame.get("time")
            frame_size = frame.get("frame_size") or frame_size

    hits = [item for item in targets if _bbox_hit(item.get("bbox"), x, y)]
    # 框重叠时返回面积最小的那个：点到围巾应该返回“围巾”，不是整个人物
    best = min(hits, key=lambda item: _bbox_area(item["bbox"])) if hits else None
    return ok({
        "hit": best is not None,
        "object": {"name": best.get("name"), "bbox": best.get("bbox"),
                   "explanation": best.get("explanation") or ""} if best else None,
        "candidates": [item.get("name") for item in hits],
        "source": source, "page_no": page_no, "keyframe_time": keyframe_time,
        "frame_size": frame_size, "point": {"x": round(x, 1), "y": round(y, 1)},
        "audio_url": None,
        "timings": {"lookup_ms": int((time.perf_counter() - started) * 1000), "tts_ms": 0},
    })


@bp.get("/history")
def history():
    _pending("对话历史", "/mock/api/v1/history")
