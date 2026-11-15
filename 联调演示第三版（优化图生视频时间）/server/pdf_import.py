"""Sequential PDF import with durable per-page decisions and temporary source storage."""
from __future__ import annotations

from contextlib import closing
import io
import json
import logging
import math
import re
import secrets
import shutil
import threading
import time
import unicodedata
from pathlib import Path

from PIL import Image

from .book_store import SCHEMA_VERSION, empty_page
from .errors import AppError, NotFoundError, TaskCancelled, ValidationError
from .run_logger import now_iso
from .uploads import validate_image

log = logging.getLogger(__name__)
_PDFIUM_LOCK = threading.Lock()
_TRASH_SECONDS = 7 * 86400


def _open_pdf(path):
    import pypdfium2 as pdfium

    try:
        doc = pdfium.PdfDocument(path)
    except pdfium.PdfiumError as exc:
        if exc.err_code == pdfium.raw.FPDF_ERR_PASSWORD:
            raise ValidationError("PDF 已加密，请上传未加密版本", code="PDF_ENCRYPTED") from exc
        raise ValidationError("PDF 无法打开，文件可能损坏", code="PDF_INVALID") from exc
    if pdfium.raw.FPDF_GetSecurityHandlerRevision(doc) >= 0:
        doc.close()
        raise ValidationError("PDF 已加密，请上传未加密版本", code="PDF_ENCRYPTED")
    return doc


def _text_layer(page, bitmap):
    import pypdfium2 as pdfium

    blocks, line, boxes = [], [], []

    def finish():
        text = "".join(line).strip()
        if text and boxes:
            blocks.append({"text": text, "bbox": [
                min(b[0] for b in boxes), min(b[1] for b in boxes),
                max(b[2] for b in boxes), max(b[3] for b in boxes)]})
        line.clear()
        boxes.clear()

    with closing(page.get_textpage()) as text_page:
        count = text_page.count_chars()
        if count > 20000:
            return None
        positions = bitmap.get_posconv(page)
        invalid = visible = 0
        for index in range(count):
            code = pdfium.raw.FPDFText_GetUnicode(text_page, index)
            char = chr(code) if 0 < code <= 0x10FFFF else "�"
            if char in "\r\n":
                finish()
                continue
            if char.isspace():
                line.append(" ")
                continue
            visible += 1
            if char == "�" or unicodedata.category(char) in {"Cc", "Co", "Cs", "Cn"}:
                invalid += 1
                continue
            left, bottom, right, top = text_page.get_charbox(index)
            corners = [positions.to_bitmap(x, y) for x in (left, right) for y in (bottom, top)]
            xs, ys = zip(*corners)
            box = [max(0, min(xs)), max(0, min(ys)),
                   min(bitmap.width, max(xs)), min(bitmap.height, max(ys))]
            if box[0] >= box[2] or box[1] >= box[3]:
                invalid += 1
                continue
            line.append(char)
            boxes.append(box)
        finish()
    text = "\n".join(b["text"] for b in blocks)
    if not visible or invalid / visible > 0.15 or sum(c.isalnum() for c in text) < 4:
        return None
    return {"full_text": text, "blocks": blocks}


def _render(path, index, output, long_edge, max_pixels):
    import pypdfium2 as pdfium

    with _PDFIUM_LOCK, closing(_open_pdf(path)) as doc, closing(doc[index]) as page:
        width, height = page.get_size()
        if not all(math.isfinite(x) and x > 0 for x in (width, height)):
            raise ValidationError("页面尺寸无效")
        # PDFium rounds each resulting dimension up. Leave one pixel of headroom.
        scale = min(200 / 72, (long_edge - 1) / max(width, height),
                    (math.sqrt(max_pixels) - 1) / math.sqrt(width * height))
        if math.ceil(width * scale) * math.ceil(height * scale) > max_pixels:
            scale = min(scale, (math.isqrt(max_pixels) - 1) / max(width, height))
        try:
            with closing(page.render(scale=scale)) as bitmap:
                if max(bitmap.width, bitmap.height) > long_edge or bitmap.width * bitmap.height > max_pixels:
                    raise ValidationError("生成页图的尺寸超出限制")
                with bitmap.to_pil() as image:
                    image.save(output, format="PNG")
                try:
                    text = _text_layer(page, bitmap)
                except (pdfium.PdfiumError, ValueError, OverflowError):
                    text = None
                return bitmap.width, bitmap.height, text
        except (pdfium.PdfiumError, ValueError, OverflowError) as exc:
            raise ValidationError("该页无法渲染，请重试或上传替换页") from exc


class PdfImports:
    def __init__(self, settings, pipeline, tasks):
        self.settings, self.pipeline, self.tasks = settings, pipeline, tasks
        # Hidden staging folders share the filesystem with the published books.
        self.root = pipeline.store.books_dir / ".pdf-imports"
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.prune()
        for path in self.root.glob("*/status.json"):
            record = json.loads(path.read_text(encoding="utf-8"))
            if pipeline.store.exists(record["id"]) and record["status"] != "trash":
                self._save({**record, "status": "completed", "has_source": False, "reason": None})
                (path.parent / "source.pdf").unlink(missing_ok=True)
            elif record["status"] in {"running", "cancelling"}:
                self._trash(record, "服务重启，上传中断")

    def _directory(self, item_id):
        if not re.fullmatch(r"pdf-[0-9a-f]{32}", item_id):
            raise NotFoundError("未找到这次 PDF 上传")
        return self.root / item_id

    def _load(self, item_id):
        path = self._directory(item_id) / "status.json"
        if not path.is_file():
            raise NotFoundError("未找到这次 PDF 上传")
        return json.loads(path.read_text(encoding="utf-8"))

    def _save(self, record):
        directory = self._directory(record["id"])
        temp = directory / "status.tmp"
        temp.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        temp.replace(directory / "status.json")

    def _public(self, record):
        keys = ("id", "name", "title", "status", "page", "page_count", "reason", "created_at",
                "expires_at", "has_source", "book_id")
        return {key: record[key] for key in keys if key in record}

    def start(self, upload, title):
        self.prune()
        item_id = "pdf-" + secrets.token_hex(16)
        name = re.split(r"[/\\]", upload.filename or "绘本.pdf")[-1][:180]
        record = {"id": item_id, "name": name, "title": title[:120] or Path(name).stem,
                  "status": "running", "page": 1, "page_count": None, "reason": None,
                  "created_at": now_iso(), "has_source": False, "book_id": item_id}
        directory = self._directory(item_id)
        directory.mkdir()
        source = directory / "source.pdf"
        limit = self.settings.pdf_max_mb * 1024**2
        try:
            with source.open("wb") as output:
                size = 0
                for chunk in iter(lambda: upload.stream.read(1024**2), b""):
                    size += len(chunk)
                    if size > limit:
                        break
                    output.write(chunk)
        except (OSError, AppError):
            source.unlink(missing_ok=True)
            with self.lock:
                self._trash(record, "PDF 未完整接收")
            raise
        with self.lock:
            if size > limit:
                source.unlink()
                return self._trash(record, f"PDF 文件超过 {self.settings.pdf_max_mb} MiB")
            record["has_source"] = True
            self._save(record)
            self._submit(record)
            return self._public(record)

    def reject(self, reason):
        with self.lock:
            item_id = "pdf-" + secrets.token_hex(16)
            self._directory(item_id).mkdir()
            return self._trash({"id": item_id, "name": "PDF 上传", "created_at": now_iso()}, reason)

    def active(self):
        with self.lock:
            return [self._public(r) for r in self._records() if r["status"] in {"running", "cancelling", "paused"}]

    def _records(self):
        return sorted((json.loads(p.read_text(encoding="utf-8")) for p in self.root.glob("*/status.json")),
                      key=lambda r: r["created_at"], reverse=True)

    def get(self, item_id):
        self.prune()
        with self.lock:
            return self._public(self._load(item_id))

    def decide(self, item_id, action, page, replacement=None):
        with self.lock:
            record = self._load(item_id)
            if action == "cancel" and record["status"] in {"running", "cancelling", "paused"}:
                if record["status"] == "paused":
                    return self._trash(record, "已取消整本上传")
                record["status"] = "cancelling"
                self._save(record)
                return self._public(record)
            if action not in {"skip", "retry"} or record["status"] != "paused" or page != record["page"]:
                raise ValidationError("当前上传状态已变化，请刷新后再操作", http_status=409)
            directory = self._directory(item_id)
            image = directory / "replacement.png"
            if replacement is not None and action == "retry":
                upload = validate_image(replacement, max_bytes=int(self.settings.max_upload_mb * 1024**2),
                                        max_pixels=self.settings.max_image_pixels, field="replacement")
                with Image.open(io.BytesIO(upload.data)) as original:
                    edge = min(self.settings.pdf_max_edge, math.isqrt(self.settings.pdf_max_pixels))
                    original.thumbnail((edge, edge))
                    original.convert("RGB").save(image, format="PNG")
            record.update(status="running", reason=None)
            if action == "skip":
                image.unlink(missing_ok=True)
                self._write_skipped(record)
            self._save(record)
            self._submit(record)
            return self._public(record)

    def _submit(self, record):
        try:
            self.tasks.submit("pdf_import", lambda _: self._run(record["id"]), book_id=record["id"])
        except Exception:
            self._trash(record, "上传任务无法启动，请重新上传")

    def _page_path(self, record):
        return self._directory(record["id"]) / "book" / "pages" / f"page_{record['page']:03}.png"

    def _write_page(self, record, image, width, height, source):
        page = empty_page(record["id"], record["page"], f"pages/{image.name}", width, height)
        page["source_reference"].update(original_filename=record["name"], pdf_page=record["page"], text_source=source)
        return page

    def _checkpoint(self, record, image, page):
        image.with_suffix(".json").write_text(json.dumps(page, ensure_ascii=False), encoding="utf-8")
        record["page"] += 1
        self._save(record)

    def _write_skipped(self, record):
        image = self._page_path(record)
        with Image.new("RGB", (640, 480), "white") as placeholder:
            placeholder.save(image)
        page = self._write_page(record, image, 640, 480, "skipped")
        page["import_skipped"] = True
        self._checkpoint(record, image, page)

    def _run(self, item_id):
        directory = self._directory(item_id)
        source = directory / "source.pdf"
        replacement = directory / "replacement.png"
        try:
            with self.lock:
                record = self._load(item_id)
                if record["status"] == "cancelling":
                    return self._trash(record, "已取消整本上传")
            if record["page_count"] is None:
                with _PDFIUM_LOCK, closing(_open_pdf(source)) as doc:
                    count = len(doc)
                if not 1 <= count <= self.settings.pdf_max_pages:
                    raise ValidationError(f"PDF 共 {count} 页，最多支持 {self.settings.pdf_max_pages} 页")
                record["page_count"] = count
            while record["page"] <= record["page_count"]:
                with self.lock:
                    if self._load(item_id)["status"] == "cancelling":
                        return self._trash(record, "已取消整本上传")
                    self._save(record)
                image = self._page_path(record)
                image.parent.mkdir(parents=True, exist_ok=True)
                try:
                    if replacement.exists():
                        shutil.copyfile(replacement, image)
                        with Image.open(image) as original:
                            width, height = original.size
                        text = None
                    else:
                        width, height, text = _render(source, record["page"] - 1, image,
                            self.settings.pdf_max_edge, self.settings.pdf_max_pixels)
                    page = self._write_page(record, image, width, height, "pdf_text_layer" if text else "ocr")
                    if text is None:
                        try:
                            result = self.pipeline.ocr.recognize_image(image, book_id=item_id, page_no=record["page"])
                        except Exception as exc:
                            message = exc.message if isinstance(exc, AppError) else "文字识别服务执行失败"
                            raise ValidationError(f"OCR 失败：{message}") from exc
                        text = {key: result[key] for key in ("full_text", "blocks")}
                        page["ocr_meta"] = {key: result.get(key) for key in ("run_id", "elapsed_ms")}
                    page["ocr_text"] = text
                except (AppError, OSError) as exc:
                    with self.lock:
                        if self._load(item_id)["status"] == "cancelling":
                            return self._trash(record, "已取消整本上传")
                        record.update(status="paused", reason=exc.message if isinstance(exc, AppError) else "无法写入当前页")
                        self._save(record)
                        return self._public(record)
                with self.lock:
                    if self._load(item_id)["status"] == "cancelling":
                        return self._trash(record, "已取消整本上传")
                    self._checkpoint(record, image, page)
                    replacement.unlink(missing_ok=True)
            with self.lock:
                if self._load(item_id)["status"] == "cancelling":
                    return self._trash(record, "已取消整本上传")
                self._publish(record)
            pages = [p["page_no"] for p in self.pipeline.store.load(item_id)["pages"] if not p.get("import_skipped")]
            if pages and self.settings.auto_generate:
                self.submit_generation(item_id)
            elif pages and self.settings.vlm_auto and self.pipeline.vlm.status()["available"]:
                self.tasks.submit("vlm", lambda ctx: self.pipeline.vlm.analyze_book(
                    item_id, pages=pages, progress=ctx.progress, check_cancelled=ctx.check_cancelled), book_id=item_id)
            return self._public(record)
        except Exception as exc:
            with self.lock:
                if self.pipeline.store.exists(item_id):
                    return self._public(self._load(item_id))
                if not isinstance(exc, AppError):
                    log.exception("PDF import %s failed", item_id)
                return self._trash(record, exc.message if isinstance(exc, AppError) else "PDF 导入中断，请重新上传")

    def submit_generation(self, book_id):
        book = self.pipeline.store.load(book_id)
        if book.get("source_reference", {}).get("origin") != "pdf_upload":
            raise ValidationError("仅 PDF 导入绘本支持自动生成")
        pages = [p["page_no"] for p in book["pages"] if not p.get("import_skipped")]
        return self.tasks.submit("auto_generate", lambda ctx: self._auto_generate(book_id, pages, ctx),
                                 book_id=book_id, dedupe_key=f"auto-generate:{book_id}")

    def _auto_vlm(self, book_id, pages, ctx):
        vlm = self.pipeline.vlm
        if not vlm.status()["available"]:
            return [{"page_no": page, "code": "VLM_UNAVAILABLE"} for page in pages]
        remaining = list(pages)
        for attempt in range(3):
            ctx.check_cancelled()
            try:
                result = vlm.analyze_book(book_id, pages=remaining,
                    progress=lambda f, m: ctx.progress(f * 0.2, m or "画面理解"),
                    check_cancelled=ctx.check_cancelled)
                errors = result.get("errors") or []
            except TaskCancelled:
                raise
            except Exception:
                log.warning("auto VLM failed for %s", book_id, exc_info=True)
                errors = [{"page_no": page, "code": "VLM_FAILED"} for page in remaining]
            if not errors or attempt == 2 or all(e.get("code") == "BUDGET_EXCEEDED" for e in errors):
                return errors
            remaining = [item["page_no"] for item in errors]
            for _ in range(15 * (attempt + 1)):
                ctx.check_cancelled()
                time.sleep(1)

    def _auto_generate(self, book_id, pages, ctx):
        pipeline = self.pipeline
        warnings = self._auto_vlm(book_id, pages, ctx)
        ctx.check_cancelled()
        narration = pipeline.narration.generate_book(book_id, pages=pages,
            progress=lambda f, m: ctx.progress(0.2 + f * 0.2, m or "生成旁白"),
            check_cancelled=ctx.check_cancelled)
        ctx.check_cancelled()
        video = pipeline.video.generate_book_video(book_id, pages=pages,
            progress=lambda f, m: ctx.progress(0.4 + f * 0.6, m or "配音、生成视频"),
            check_cancelled=ctx.check_cancelled)
        return {"book_id": book_id, "vlm_errors": warnings, "narration": narration, "video": video}

    def _publish(self, record):
        directory = self._directory(record["id"])
        draft = directory / "book"
        pages = []
        for path in sorted((draft / "pages").glob("*.json")):
            pages.append(json.loads(path.read_text(encoding="utf-8")))
        if not any(not p.get("import_skipped") for p in pages):
            raise ValidationError("所有页面均已跳过，未创建绘本")
        metadata = {"schema_version": SCHEMA_VERSION, "book_id": record["id"], "title": record["title"],
                    "language": "zh", "age_range": None, "created_at": record["created_at"], "updated_at": now_iso(),
                    "source_reference": {"origin": "pdf_upload", "original_filename": record["name"]},
                    "characters": [], "pages": pages, "video": None}
        (draft / "book.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        for path in (draft / "pages").glob("*.json"):
            path.unlink()
        draft.rename(self.pipeline.store.book_dir(record["id"]))
        record.update(status="completed", reason=None, has_source=False)
        self._save(record)
        (directory / "source.pdf").unlink(missing_ok=True)

    def _trash(self, record, reason):
        directory = self._directory(record["id"])
        record.update(status="trash", reason=reason, expires_at=time.time() + _TRASH_SECONDS,
                      has_source=(directory / "source.pdf").is_file())
        self._save(record)
        draft = directory / "book"
        if draft.exists():
            shutil.rmtree(draft)
        (directory / "replacement.png").unlink(missing_ok=True)
        return self._public(record)

    def prune(self):
        with self.lock:
            for path in self.root.glob("*/status.json"):
                record = json.loads(path.read_text(encoding="utf-8"))
                if record["status"] == "trash" and record["expires_at"] <= time.time():
                    shutil.rmtree(path.parent)

    def trash(self):
        self.prune()
        with self.lock:
            return [self._public(r) for r in self._records() if r["status"] == "trash"]

    def original(self, item_id):
        self.prune()
        with self.lock:
            record = self._load(item_id)
            path = self._directory(item_id) / "source.pdf"
            if record["status"] != "trash" or not path.is_file():
                raise NotFoundError("没有可下载的原文件")
            return path.open("rb"), record["name"]

    def delete(self, item_id):
        with self.lock:
            if self._load(item_id)["status"] != "trash":
                raise NotFoundError("垃圾桶里没有这个文件")
            shutil.rmtree(self._directory(item_id))
