# -*- coding: utf-8 -*-
"""真实接口：路径前缀 /api/v1。字段与假接口（/mock/api/v1）一致。

已接入真实实现：绘本上传与读取、旁白生成、视频生成、任务状态、运行记录与费用查询。
已接入：点击识物（查表主方案，物体框来自分页数据）。
尚未接入（返回 501 NOT_IMPLEMENTED，可先用假接口）：语音识别、语音合成、问答、对话历史
——它们依赖 B 的检索与识别模块和 A 后续的语音链路（步骤 6 以后）。
"""
from __future__ import annotations

import time
from typing import Optional

from flask import Blueprint, current_app, request, send_file

from . import touch
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
    内容理解字段（OCR、物体、摘要等）留空，等 B 的模块填充。
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
    return ok({"book_id": book["book_id"], "title": book["title"], "page_count": len(uploads),
               "accepted_pages": [{"page_no": i, "filename": u.original_name, "bytes": u.size,
                                   "width": u.width, "height": u.height} for i, u in enumerate(uploads, 1)],
               "task_id": None, "note": "页面已保存。OCR 与画面理解由内容理解模块完成后写入分页数据"}, 201)


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
    _pending("问答", "/mock/api/v1/qa/ask")


@bp.post("/touch/identify")
def touch_identify():
    """点击识物（查表主方案）。物体框来自分页数据；页面还没有标注时 hit=false 并说明原因。"""
    body = _body()
    book_id = check_book_id(body.get("book_id"))
    page_no = check_page_no(body.get("page_no"))
    try:
        x, y = float(body["x"]), float(body["y"])
    except (KeyError, TypeError, ValueError):
        raise ValidationError("缺少点击坐标 x、y", details={"field": "x,y"}) from None
    play_time = body.get("play_time")
    try:
        play_time = float(play_time) if play_time not in (None, "") else None
    except (TypeError, ValueError):
        raise ValidationError("play_time 应为数字", details={"field": "play_time"}) from None
    book = _store().load(book_id)
    page = BookStore.get_page(book, page_no)
    result = touch.identify(book, page, x, y, play_time=play_time, coord_space=str(body.get("coord_space", "image")))
    result["audio_url"] = None      # 讲解语音需要 TTS 链路，后续接入
    return ok(result)


@bp.get("/history")
def history():
    _pending("对话历史", "/mock/api/v1/history")
