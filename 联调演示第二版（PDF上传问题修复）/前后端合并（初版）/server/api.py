# -*- coding: utf-8 -*-
"""真实接口：路径前缀 /api/v1。字段与假接口（/mock/api/v1）一致。

已接入真实实现：绘本上传与读取、本地 OCR 文字识别（RapidOCR）、本地语音识别（SenseVoice）、
旁白生成、视频生成、任务状态、运行记录与费用查询。
已接入：点击识物（查表主方案，物体框来自分页数据）、有依据问答（检索 + 本地 Qwen3）、
多模态对话历史（按 session 落盘）、语音合成与声音复刻。
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import secrets
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional

from flask import Blueprint, current_app, request, send_file

from agents import content_review

from . import touch
from .book_store import BookStore, check_book_id, check_page_no, empty_page, summarize_book, with_urls
from .envelope import begin_request, ok
from .errors import (ASRUnavailable, AppError, NotFoundError, NotImplementedYet, OCRUnavailable,
                     ValidationError, VLMUnavailable, VoiceUnavailable)
from .history_store import check_session_id
from .voice_store import load_selection, save_selection, selected_voice_id
from .task_queue import TaskContext, public_task
from .uploads import validate_audio, validate_image

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
    pipeline = _ext()["pipeline"]
    ocr = pipeline.ocr.status()
    asr = pipeline.asr.status()
    vlm = pipeline.vlm.status()
    return ok({"service": "paper-book-smart-companion", "mode": "real",
               "services": summary["services"], "limits": summary["limits"], "video": summary["video"],
               "ocr": ocr, "asr": asr, "vlm": vlm, "voices": pipeline.voices.status(),
               "tts_provider": pipeline.tts.provider,
               "i2v_provider": summary["i2v_provider"], "dashscope": summary["dashscope"],
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
@bp.post("/pdf-imports")
def start_pdf_import():
    imports = _ext()["pdf_imports"]
    limit = _ext()["settings"].pdf_max_mb
    if (request.content_length or 0) > (limit + 1) * 1024**2:
        return ok(imports.reject(f"PDF 文件超过 {limit} MiB"), 202)
    from werkzeug.exceptions import BadRequest, RequestEntityTooLarge
    try:
        uploads = request.files.getlist("pdf")
    except (BadRequest, RequestEntityTooLarge):
        return ok(imports.reject("文件未完整接收或超过上传上限"), 202)
    if len(uploads) != 1 or not uploads[0].filename:
        raise ValidationError("请选择一个 PDF 文件")
    return ok(imports.start(uploads[0], (request.form.get("title") or "").strip()), 202)


@bp.get("/pdf-imports")
def active_pdf_imports():
    return ok({"items": _ext()["pdf_imports"].active()})


@bp.get("/pdf-imports/<item_id>")
def get_pdf_import(item_id):
    return ok(_ext()["pdf_imports"].get(item_id))


@bp.post("/pdf-imports/<item_id>/decision")
def decide_pdf_import(item_id):
    body = _body()
    page = check_page_no(body["page"]) if "page" in body else None
    return ok(_ext()["pdf_imports"].decide(item_id, body.get("action"), page, request.files.get("replacement")))


@bp.get("/pdf-trash")
def list_pdf_trash():
    return ok({"items": _ext()["pdf_imports"].trash()})


@bp.get("/pdf-trash/<item_id>/original")
def download_pdf_trash(item_id):
    source, name = _ext()["pdf_imports"].original(item_id)
    response = send_file(source, mimetype="application/octet-stream", as_attachment=True,
                         download_name=name, max_age=0)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.delete("/pdf-trash/<item_id>")
def delete_pdf_trash(item_id):
    _ext()["pdf_imports"].delete(item_id)
    return ok({"deleted": True})


@bp.get("/books")
def list_books():
    store = _store()
    books = [summarize_book(_reviewed_book(store.load(book_id)), _media_url_for(book_id)) for book_id in store.list_ids()]
    return ok({"books": books})


@bp.post("/books")
def upload_book():
    """上传一本绘本的页面图片（multipart，字段 pages 可重复，按上传顺序编页码）。

    全部文件先校验，任何一个不合格就整体拒绝，不会留下半本书。
    上传成功后默认自动提交本地 OCR 任务（可用表单字段 ocr=0 关闭，或改 PBSC_OCR_AUTO）；
    识别结果写回分页数据的 `ocr_text`。人物、物体、场景等画面理解字段仍留空，等 VLM 模块填充。
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

    pipeline, tasks = _ext()["pipeline"], _ext()["tasks"]
    auto_ocr = _flag(request.form.get("ocr", settings.ocr_auto))
    task = None
    if auto_ocr:
        ocr = pipeline.ocr
        ocr_status = ocr.status()
        if ocr_status["available"]:
            def ocr_job(ctx: TaskContext) -> dict:
                return ocr.recognize_book(book["book_id"], progress=ctx.progress,
                                          check_cancelled=ctx.check_cancelled)

            task = tasks.submit("ocr", ocr_job, params={"book_id": book["book_id"], "page_no": None,
                                                        "force": False},
                                book_id=book["book_id"], dedupe_key=f"ocr:{book['book_id']}:all")
            note = "页面已保存，文字识别任务已开始；画面理解（人物/物体/场景）仍需 VLM 模块"
        else:
            note = ("页面已保存，但本地 OCR 依赖未就绪，未启动文字识别；"
                    "可查看 /api/v1/health 的 ocr 字段")
    else:
        note = f"页面已保存。可调用 POST /api/v1/books/{book['book_id']}/ocr 做文字识别"

    vlm_task = None
    auto_vlm = _flag(request.form.get("vlm", settings.vlm_auto))
    if auto_vlm:
        vlm_agent = pipeline.vlm
        if vlm_agent.status()["available"]:
            def vlm_job(ctx: TaskContext) -> dict:
                return vlm_agent.analyze_book(book["book_id"], progress=ctx.progress,
                                              check_cancelled=ctx.check_cancelled)

            vlm_task = tasks.submit("vlm", vlm_job, params={"book_id": book["book_id"], "page_no": None,
                                                            "force": False},
                                    book_id=book["book_id"], dedupe_key=f"vlm:{book['book_id']}:all")
            note += "；画面理解任务已开始"
        else:
            note += "；VLM 未配置，未启动画面理解"

    return ok({"book_id": book["book_id"], "title": book["title"], "page_count": len(uploads),
               "accepted_pages": [{"page_no": i, "filename": u.original_name, "bytes": u.size,
                                   "width": u.width, "height": u.height} for i, u in enumerate(uploads, 1)],
               "task_id": task["task_id"] if task else None,
               "vlm_task_id": vlm_task["task_id"] if vlm_task else None,
               "task_ids": {"ocr": task["task_id"] if task else None,
                            "vlm": vlm_task["task_id"] if vlm_task else None},
               "note": note}, 201)


@bp.post("/books/<book_id>/ocr")
def ocr_book(book_id: str):
    """对整本或指定页做本地文字识别（RapidOCR），写回 `ocr_text` 和 `ocr_meta`。

    请求体可选 `page_no`（只识别该页）和 `force`（忽略已有结果重新识别）。
    引擎在第一次任务执行时加载，首次运行会将模型下载/读取到 `PBSC_OCR_MODEL_DIR`。
    """
    body = _body()
    book_id = check_book_id(book_id)
    page_no = check_page_no(body["page_no"]) if body.get("page_no") not in (None, "") else None
    book = _store().load(book_id)
    if page_no is not None:
        BookStore.get_page(book, page_no)
    force = _flag(body.get("force", False))
    pipeline, tasks = _ext()["pipeline"], _ext()["tasks"]
    ocr = pipeline.ocr
    ocr_status = ocr.status()
    if not ocr_status["available"]:
        raise OCRUnavailable("本地 OCR 模块未就绪，请检查依赖与 third_party/RapidOCR 部署",
                             details=ocr_status)

    def job(ctx: TaskContext) -> dict:
        if page_no is not None:
            ctx.progress(0.1, f"第 {page_no} 页文字识别")
            result = ocr.recognize_page(book_id, page_no, force)
            return {"run_id": result["run_id"], **result}
        return ocr.recognize_book(book_id, force=force, progress=ctx.progress,
                                  check_cancelled=ctx.check_cancelled)

    task = tasks.submit("ocr", job, params={"book_id": book_id, "page_no": page_no, "force": force},
                        book_id=book_id, page_no=page_no, dedupe_key=f"ocr:{book_id}:{page_no or 'all'}")
    return ok(public_task(task), 202)


@bp.post("/books/<book_id>/vlm")
def vlm_book(book_id: str):
    """对整本或指定页运行 VLM，写回画面描述、对象框和可选情节。

    请求体可选 `page_no`、`force`。VLM 只被要求返回它确实具备的能力：
    page_no、scene_summary、objects[].{name,bbox}；contours/plot/confidence 可选。
    """
    body = _body()
    book_id = check_book_id(book_id)
    page_no = check_page_no(body["page_no"]) if body.get("page_no") not in (None, "") else None
    book = _store().load(book_id)
    if page_no is not None:
        BookStore.get_page(book, page_no)
    force = _flag(body.get("force", False))
    pipeline, tasks = _ext()["pipeline"], _ext()["tasks"]
    vlm = pipeline.vlm
    status = vlm.status()
    if not status["available"]:
        raise VLMUnavailable("VLM 服务未配置或未就绪，请设置 VLM_API_URL", details=status)

    def job(ctx: TaskContext) -> dict:
        if page_no is not None:
            ctx.progress(0.1, f"第 {page_no} 页画面理解")
            result = vlm.analyze_page(book_id, page_no, force)
            return {"run_id": result.get("run_id"), **result}
        return vlm.analyze_book(book_id, force=force, progress=ctx.progress,
                                check_cancelled=ctx.check_cancelled)

    task = tasks.submit("vlm", job, params={"book_id": book_id, "page_no": page_no, "force": force},
                        book_id=book_id, page_no=page_no, dedupe_key=f"vlm:{book_id}:{page_no or 'all'}")
    return ok(public_task(task), 202)


def _reviewed_book(book):
    if not content_review.enabled():
        return book
    book_id = book["book_id"]
    record = content_review.read_record(content_review.review_path(_ext()["settings"].data_dir, book_id))
    try:
        allowed = bool(record.get("human")) and content_review.approved(record, content_review.fingerprint(_store(), book))
    except (AppError, OSError):
        allowed = False
    if allowed:
        return book
    hidden = {"narration", "narration_audio", "video_meta", "video_clip_id", "time_range", "keyframes"}
    return {**book, "video": None, "pages": [{k:v for k,v in p.items() if k not in hidden} for p in book["pages"]],
            "review_pending": True}


@bp.get("/books/<book_id>")
def get_book(book_id: str):
    book = _reviewed_book(_store().load(check_book_id(book_id)))
    url = _media_url_for(book_id)
    video = dict(book.get("video") or {})
    if video.get("path"):
        video["url"] = url(video["path"])
        video["clips"] = [{**c, "clip_url": url(c["clip_path"])} for c in video.get("clips", [])]
    data = {k: book.get(k) for k in ("schema_version", "book_id", "title", "language", "age_range",
                                     "source_reference", "characters")}
    data.update(page_count=len(book.get("pages", [])), video=video or None, review_pending=book.get("review_pending", False))
    return ok(data)


@bp.get("/books/<book_id>/pages")
def list_pages(book_id: str):
    book = _reviewed_book(_store().load(check_book_id(book_id)))
    return ok({"book_id": book_id, "pages": [with_urls(p, _media_url_for(book_id)) for p in book.get("pages", [])]})


@bp.get("/books/<book_id>/pages/<page_no>")
def get_page(book_id: str, page_no: str):
    book = _reviewed_book(_store().load(check_book_id(book_id)))
    return ok(with_urls(BookStore.get_page(book, check_page_no(page_no)), _media_url_for(book_id)))


@bp.get("/media/<book_id>/<path:relative>")
def media(book_id: str, relative: str):
    path = _store().resolve_media(check_book_id(book_id), relative)
    if content_review.enabled() and path.suffix.lower() in (".mp4", ".wav", ".mp3", ".m4a", ".webm"):
        book = _reviewed_book(_store().load(book_id))
        allowed = [(book.get("video") or {}).get("path")]
        for page in book["pages"]:
            allowed.extend([(page.get("video_meta") or {}).get("clip_path"),
                            (page.get("narration_audio") or {}).get("path")])
        if relative not in allowed:
            raise AppError("生成媒体等待人工复核", code="REVIEW_PENDING", http_status=403)
    return send_file(path, conditional=True)


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


@bp.post("/generate/audio")
def generate_audio():
    """提交旁白配音任务（不生成视频）。page_no 省略表示整本；force=true 忽略缓存。"""
    body = _body()
    book_id, page_no = _target(body)
    force = _flag(body.get("force", False))
    pipeline, tasks = _ext()["pipeline"], _ext()["tasks"]

    def job(ctx: TaskContext) -> dict:
        if page_no:
            ctx.progress(0.1, f"第 {page_no} 页配音")
            return pipeline.video.generate_page_audio(book_id, page_no, force)
        book = pipeline.store.load(book_id)
        targets = [p["page_no"] for p in book.get("pages") or []]
        results, errors = [], []
        for index, number in enumerate(targets, start=1):
            ctx.check_cancelled()
            ctx.progress((index - 1) / max(1, len(targets)), f"第 {number} 页配音")
            try:
                results.append(pipeline.video.generate_page_audio(book_id, number, force))
            except AppError as exc:
                errors.append({"page_no": number, **exc.to_dict()})
        ctx.progress(1.0, "配音完成")
        if not results and errors:
            raise AppError("整本配音全部失败", code="AUDIO_FAILED", http_status=502,
                           details={"errors": errors[:3]})
        return {"book_id": book_id, "run_id": results[0].get("run_id") if results else None,
                "page_count": len(targets), "succeeded": len(results), "failed": len(errors),
                "pages": results, "errors": errors}

    task = tasks.submit("audio", job, params={"book_id": book_id, "page_no": page_no, "force": force},
                        book_id=book_id, page_no=page_no, dedupe_key=f"audio:{book_id}:{page_no or 'all'}")
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
    """语音识别（本地 SenseVoice ONNX）。multipart 字段 audio。

    可选表单/JSON 字段：`language`（auto/zh/en/yue/ja/ko/nospeech）、`book_id`、`page_no`。
    返回 text、raw_text、rich_text、language、emotion、event、duration、elapsed_ms、run_id。
    """
    settings = _ext()["settings"]
    upload = validate_audio(request.files.get("audio"),
                            max_bytes=int(settings.max_upload_mb * 1024 * 1024), field="audio")
    body = _body()
    language = str(body.get("language") or settings.asr_language).strip().lower()
    book_id = check_book_id(body["book_id"]) if body.get("book_id") else None
    page_no = check_page_no(body["page_no"]) if body.get("page_no") not in (None, "") else None
    agent = _ext()["pipeline"].asr
    status = agent.status()
    if not status["available"]:
        raise ASRUnavailable("本地语音识别模块未就绪，请检查依赖、third_party/SenseVoice 与模型文件",
                             details=status)
    with tempfile.TemporaryDirectory(prefix="pbsc_asr_upload_") as tmp:
        path = Path(tmp) / f"input{upload.ext}"
        path.write_bytes(upload.data)
        result = agent.transcribe_file(path, language=language, book_id=book_id, page_no=page_no,
                                       max_seconds=settings.asr_max_seconds)
    result.update(audio_bytes=upload.size, audio_format=upload.format,
                  timings={"asr_ms": result.get("elapsed_ms", 0)})
    return ok(result)


@bp.post("/tts")
def tts():
    """语音合成（CosyVoice WebSocket 或参考 TTS）。JSON 字段 text，可选 voice、force。

    音频保存在 data/tts/ 并按“文字+音色+provider+model”缓存；返回可直接播放的 audio_url。
    未配置真实服务时返回占位音频并标记 is_placeholder=true。
    """
    settings = _ext()["settings"]
    body = _body()
    text = str(body.get("text") or "").strip()
    if not text:
        raise ValidationError("缺少 text", details={"field": "text"})
    if len(text) > 20000:
        raise ValidationError("单次合成文本不能超过 20000 字", details={"field": "text"})
    pipeline = _ext()["pipeline"]
    client = pipeline.tts
    voice = str(body.get("voice") or selected_voice_id(settings.voice_selection_file)
                or getattr(client, "voice", "") or settings.tts_voice).strip()
    force = _flag(body.get("force", False))
    key = hashlib.sha256(f"{text}|{voice}|{client.provider}|{getattr(client, 'model', None)}"
                         .encode("utf-8")).hexdigest()[:20]
    path = settings.tts_dir / f"tts_{key}.wav"
    sidecar = path.with_suffix(".json")
    if not force and path.is_file() and sidecar.is_file():
        meta = json.loads(sidecar.read_text(encoding="utf-8"))
        audio_url = f"/api/v1/tts-audio/{path.name}"
        return ok({"text": text, "voice": meta.get("voice", voice), "audio_url": audio_url,
                   "duration": meta.get("duration"), "is_placeholder": meta.get("is_placeholder", False),
                   "model": meta.get("model"), "cached": True,
                   "timings": {"tts_ms": 0}})

    with pipeline.runs.start("tts", {"text": text, "voice": voice, "source": "api"}) as run:
        run.set_model(getattr(client, "model", None), {"voice": voice}, provider=client.provider)
        result = client.synthesize(text, path, voice, trace_id=run.run_id)
        cost = pipeline.ledger.record(
            "tts", getattr(client, "model", None),
            {"chars": len(text), "calls": 1}, run_id=run.run_id,
            billable=not result.is_placeholder, note=result.provider)
        run.set_cost(cost)
        run.add_output(path, role="tts_audio", duration=result.duration)
        run.note("result", {"voice": result.voice, "duration": result.duration,
                            "is_placeholder": result.is_placeholder})
    meta = {"voice": result.voice, "duration": result.duration,
            "is_placeholder": result.is_placeholder, "model": getattr(client, "model", None)}
    sidecar.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return ok({"text": text, "voice": result.voice,
               "audio_url": f"/api/v1/tts-audio/{path.name}",
               "duration": result.duration, "is_placeholder": result.is_placeholder,
               "model": getattr(client, "model", None), "cached": False,
               "timings": {"tts_ms": result.elapsed_ms}})


@bp.get("/tts-audio/<path:name>")
def tts_audio(name: str):
    """读取 /tts 生成的音频文件（只允许 data/tts/ 内的文件）。"""
    base = _ext()["settings"].tts_dir.resolve()
    target = (base / name).resolve()
    if base not in target.parents and target != base:
        raise ValidationError("非法的文件路径", code="INVALID_PATH")
    if not target.is_file():
        raise NotFoundError("音频不存在", code="FILE_NOT_FOUND", details={"path": name})
    return send_file(target, conditional=True)


# ---------------------------------------------------------------- 音色复刻
def _voice_manager():
    manager = _ext()["pipeline"].voices
    status = manager.status()
    if not status["configured"]:
        raise VoiceUnavailable("音色复刻服务未配置，请填写 COSYVOICE_API_KEY / DASHSCOPE_API_KEY",
                               details=status)
    return manager


def _check_reference_audio(upload) -> None:
    if upload.format not in ("wav", "mp3", "mp4"):
        raise ValidationError("参考音频支持 WAV / MP3 / M4A", code="AUDIO_TYPE_NOT_ALLOWED",
                              details={"field": "audio"})
    if upload.format == "wav":
        try:
            import soundfile as sf
            info = sf.info(io.BytesIO(upload.data))
            if info.duration < 5 or info.duration > 60:
                raise ValidationError(
                    f"参考音频时长 {info.duration:.1f} 秒，应在 5–60 秒之间",
                    code="AUDIO_TOO_SHORT_OR_LONG", details={"field": "audio",
                                                             "duration": round(info.duration, 2)})
        except ValidationError:
            raise
        except Exception:       # noqa: BLE001 - 非 wav 交给服务端校验
            pass


@bp.get("/voices/cosyvoice")
def list_cosyvoice_voices():
    prefix = request.args.get("prefix") or None
    page_index = int(request.args.get("page_index", 0))
    page_size = min(100, max(1, int(request.args.get("page_size", 20))))
    return ok(_voice_manager().list_voices(prefix, page_index, page_size))


@bp.get("/voices/cosyvoice/selection")
def get_cosyvoice_selection():
    settings = _ext()["settings"]
    return ok({"selection": load_selection(settings.voice_selection_file),
               "voice_id": selected_voice_id(settings.voice_selection_file)})


@bp.post("/voices/cosyvoice/selection")
def select_cosyvoice_voice():
    body = _body()
    voice_id = str(body.get("voice_id") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9._:-]{1,200}", voice_id):
        raise ValidationError("voice_id 格式不正确", details={"field": "voice_id"})
    settings = _ext()["settings"]
    selection = save_selection(settings.voice_selection_file, voice_id,
                               model=body.get("model"), name=body.get("name"))
    return ok({"selection": selection, "voice_id": voice_id})


@bp.post("/voices/cosyvoice")
def create_cosyvoice_voice():
    """上传 5–60 秒参考音频，复刻一个音色；返回 voice_id 和审核状态。"""
    settings = _ext()["settings"]
    manager = _voice_manager()
    upload = validate_audio(request.files.get("audio"), max_bytes=10 * 1024 * 1024, field="audio")
    if upload.format not in ("wav", "mp3", "mp4"):
        import imageio_ffmpeg
        with tempfile.TemporaryDirectory() as directory:
            original = Path(directory) / ("source" + upload.ext)
            wav = Path(directory) / "reference.wav"
            original.write_bytes(upload.data)
            try:
                subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),"-loglevel","error","-i",str(original),
                                "-t","61","-ac","1","-ar","16000",str(wav)],
                               check=True,timeout=25,capture_output=True)
            except (OSError,subprocess.SubprocessError) as exc:
                raise ValidationError("录音无法转换，请使用 WAV、MP3 或 M4A") from exc
            from types import SimpleNamespace
            upload = SimpleNamespace(format="wav",data=wav.read_bytes(),mime="audio/wav")
    _check_reference_audio(upload)
    prefix = re.sub(r"[^A-Za-z0-9_-]", "", (request.form.get("prefix") or "pbsc").strip())[:10] or "pbsc"
    target_model = (request.form.get("target_model") or settings.cosyvoice_model).strip()
    hints = [item.strip() for item in (request.form.get("language_hints") or "zh").split(",")
             if item.strip()] or ["zh"]
    result = manager.create_voice(upload.data, upload.mime, prefix, hints)
    return ok({"voice": result, "voice_id": result.get("voice_id"),
               "status": result.get("status"), "target_model": result.get("target_model") or target_model}, 202)


@bp.get("/voices/cosyvoice/<voice_id>")
def get_cosyvoice_voice(voice_id: str):
    if not re.fullmatch(r"[A-Za-z0-9._:-]{1,200}", voice_id):
        raise ValidationError("voice_id 格式不正确", details={"field": "voice_id"})
    return ok(_voice_manager().get_voice(voice_id))


@bp.put("/voices/cosyvoice/<voice_id>")
def update_cosyvoice_voice(voice_id: str):
    if not re.fullmatch(r"[A-Za-z0-9._:-]{1,200}", voice_id):
        raise ValidationError("voice_id 格式不正确", details={"field": "voice_id"})
    upload = validate_audio(request.files.get("audio"), max_bytes=10 * 1024 * 1024, field="audio")
    _check_reference_audio(upload)
    return ok(_voice_manager().update_voice(voice_id, upload.data, upload.mime))


@bp.delete("/voices/cosyvoice/<voice_id>")
def delete_cosyvoice_voice(voice_id: str):
    if not re.fullmatch(r"[A-Za-z0-9._:-]{1,200}", voice_id):
        raise ValidationError("voice_id 格式不正确", details={"field": "voice_id"})
    return ok(_voice_manager().delete_voice(voice_id))


@bp.post("/qa/ask")
def qa_ask():
    """有依据的绘本问答。文字用 JSON/表单字段 question；语音用 multipart 字段 audio（先本地 ASR）。

    返回字段与假接口一致：answer_text、grounded、evidence（页码 + 视频时间点）、answer_audio_url、
    question_audio_url、timings。书里找不到依据时 grounded=false、evidence=[]。
    """
    settings = _ext()["settings"]
    pipeline = _ext()["pipeline"]
    body = _body()
    book_id = check_book_id(body.get("book_id"))
    page_no = check_page_no(body.get("page_no"))
    book = _reviewed_book(_store().load(book_id))
    BookStore.get_page(book, page_no)
    play_time = body.get("play_time")
    try:
        play_time = float(play_time) if play_time not in (None, "") else None
    except (TypeError, ValueError):
        raise ValidationError("play_time 应为数字", details={"field": "play_time"}) from None
    session_id = check_session_id(body.get("session_id") or f"session-{secrets.token_hex(4)}")

    question = str(body.get("question") or "").strip()
    upload, asr_ms = None, 0
    if request.files.get("audio"):
        upload = validate_audio(request.files.get("audio"),
                                max_bytes=int(settings.max_upload_mb * 1024 * 1024), field="audio")
        status = pipeline.asr.status()
        if not status["available"]:
            raise ASRUnavailable("本地语音识别模块未就绪，请检查依赖、third_party/SenseVoice 与模型文件",
                                 details=status)
        with tempfile.TemporaryDirectory(prefix="pbsc_qa_asr_") as tmp:
            path = Path(tmp) / f"question{upload.ext}"
            path.write_bytes(upload.data)
            transcribed = pipeline.asr.transcribe_file(path, book_id=book_id, page_no=page_no,
                                                       max_seconds=settings.asr_max_seconds)
        question = (transcribed.get("text") or "").strip() or question
        asr_ms = int(transcribed.get("elapsed_ms") or 0)
        if not question:
            raise ValidationError("没有识别出问题，请再说一次或改用文字提问", code="ASR_EMPTY",
                                  details={"field": "audio", "raw_text": transcribed.get("raw_text")})
    if not question:
        raise ValidationError("缺少 question（或上传 audio）", details={"field": "question"})
    if len(question) > 200:
        raise ValidationError("问题太长，请控制在 200 字以内", details={"field": "question"})

    turn = pipeline.qa.ask(book, page_no, question, play_time=play_time,
                           media_url=_media_url_for(book_id), asr_ms=asr_ms, session_id=session_id)
    if upload is not None:
        name = f"{turn['turn_id']}{upload.ext}"
        (settings.qa_dir / name).write_bytes(upload.data)
        turn["question_audio_url"] = f"/api/v1/qa-audio/{name}"
    pipeline.history.add(session_id, turn)
    return ok(turn)


@bp.get("/qa-audio/<path:name>")
def qa_audio(name: str):
    """读取问答时上传的问题录音（只允许 data/qa/ 内的文件）。"""
    base = _ext()["settings"].qa_dir.resolve()
    target = (base / name).resolve()
    if base not in target.parents and target != base:
        raise ValidationError("非法的文件路径", code="INVALID_PATH")
    if not target.is_file():
        raise NotFoundError("录音不存在", code="FILE_NOT_FOUND", details={"path": name})
    return send_file(target, conditional=True)


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
    book = _reviewed_book(_store().load(book_id))
    page = BookStore.get_page(book, page_no)
    result = touch.identify(book, page, x, y, play_time=play_time, coord_space=str(body.get("coord_space", "image")))
    result["audio_url"] = None      # 讲解语音需要 TTS 链路，后续接入
    return ok(result)


@bp.get("/history")
def history():
    """多模态对话历史：按 session_id 读取已落盘的问答轮次（问题、回答、依据、录音、截图）。"""
    session_id = check_session_id(request.args.get("session_id"))
    try:
        limit = min(500, max(1, int(request.args.get("limit", 200))))
    except (TypeError, ValueError):
        raise ValidationError("limit 应为正整数", details={"field": "limit"}) from None
    turns = _ext()["pipeline"].history.list(session_id, limit=limit)
    return ok({"session_id": session_id, "turns": turns})
