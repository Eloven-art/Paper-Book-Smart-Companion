# -*- coding: utf-8 -*-
"""OCR 内容理解节点（B 的模块）：用本地 RapidOCR 识别页面文字并写回分页数据。

- 推理在本地完成（ONNX Runtime CPU），不调用外部服务，不产生费用。
- 源码随项目放在 `third_party/RapidOCR/`，通过 `sys.path` 引入，避免和 pip 安装的同名包混淆。
- 模型放在 `data/models/rapidocr/`（可用 `scripts/download_ocr_models.py` 预下载）。
- 只写 `ocr_text` 与 `ocr_meta`；`objects`、`scene_summary`、`characters` 等画面理解字段仍由 VLM 模块负责。
- `ocr_text.blocks` 的 `bbox` 统一成轴对齐的 `[x1, y1, x2, y2]`，单位是页面图片像素，和 `image_size` 一致。

字段约定见 `docs/接口约定_v0.1.md` 第 4 节。
"""
from __future__ import annotations

import importlib.util
import re
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from server.book_store import BookStore
from server.errors import AppError, OCRFailed, OCRUnavailable
from server.run_logger import RunLogger, now_iso

VENDOR_ROOT = Path(__file__).resolve().parent.parent / "third_party" / "RapidOCR"
if str(VENDOR_ROOT) not in sys.path:
    sys.path.insert(0, str(VENDOR_ROOT))

# 只检查“能不能 import”，不在这里加载模型；健康检查要足够轻。
_REQUIRED_MODULES = ("onnxruntime", "cv2", "numpy", "pyclipper", "shapely", "six", "yaml",
                     "omegaconf", "colorlog")
_MODEL_LABEL = "RapidOCR PP-OCRv6 det/rec small + PP-OCRv4 cls mobile"
# 至少要有一个汉字、字母或数字，纯装饰符号（★、_、—— 等）不当作正文。
_TEXT_RE = re.compile(r"[0-9A-Za-z㐀-䶿一-鿿]")


def _box_to_bbox(box) -> list[int]:
    """RapidOCR 返回四点框（可能带旋转），转成轴对齐的 [x1, y1, x2, y2]。"""
    try:
        points = [(float(p[0]), float(p[1])) for p in box]
    except (TypeError, IndexError):
        flat = [float(v) for v in box]
        points = list(zip(flat[0::2], flat[1::2]))
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return [int(round(min(xs))), int(round(min(ys))), int(round(max(xs))), int(round(max(ys)))]


def _repair_text(text: str) -> str:
    """修补常见的标点误识别：有左双引号却把右双引号识别成结尾的“99”。"""
    if "“" in text and "”" not in text:
        text = re.sub(r"99$", "”", text)
    return text


def _repair_line(line: list[dict]) -> None:
    """行级修补：右双引号有时会被单独检测成一个“99”块，按上下文改回引号。"""
    joined = "".join(block["text"] for block in line)
    if "“" not in joined or "”" in joined:
        return
    for block in reversed(line):
        text = block["text"]
        if text == "99":
            block["text"] = "”"
            return
        if text.endswith("99"):
            block["text"] = text[:-2] + "”"
            return


def _reading_lines(blocks: list[dict]) -> list[list[dict]]:
    """按“先上后下、同一行先左后右”的阅读顺序分行。

    绘本以单栏文字为主，这里用纵向中心点做简单的行聚类；不做复杂的多栏版面分析。
    """
    ordered = sorted(blocks, key=lambda b: ((b["bbox"][1] + b["bbox"][3]) / 2, b["bbox"][0]))
    lines: list[dict] = []
    for block in ordered:
        _, y1, _, y2 = block["bbox"]
        center, height = (y1 + y2) / 2, max(1, y2 - y1)
        target = None
        for line in lines:
            tolerance = max(12.0, 0.5 * max(height, line["height"]))
            if abs(center - line["center"]) <= tolerance:
                target = line
                break
        if target is None:
            lines.append({"center": center, "height": height, "items": [block]})
        else:
            target["items"].append(block)
            centers = [(b["bbox"][1] + b["bbox"][3]) / 2 for b in target["items"]]
            target["center"] = sum(centers) / len(centers)
            target["height"] = max(target["height"], height)
    lines.sort(key=lambda line: line["center"])
    for line in lines:
        line["items"].sort(key=lambda b: b["bbox"][0])
    return [line["items"] for line in lines]


def normalize_result(result) -> dict:
    """把 RapidOCR 的输出整理成接口约定的 ocr_text。"""
    boxes = getattr(result, "boxes", None)
    texts = getattr(result, "txts", None) or ()
    scores = getattr(result, "scores", None) or ()
    blocks: list[dict] = []
    if boxes is not None:
        for box, text, score in zip(boxes, texts, scores):
            text = _repair_text(str(text).strip())
            if not text or not _TEXT_RE.search(text):
                continue
            blocks.append({"text": text, "bbox": _box_to_bbox(box), "score": round(float(score), 4)})
    lines = _reading_lines(blocks)
    for line in lines:
        _repair_line(line)
    ordered = [block for line in lines for block in line]
    full_text = "\n".join("".join(block["text"] for block in line) for line in lines)
    return {"full_text": full_text, "blocks": ordered}


class OCRAgent:
    """单页 / 整本的文字识别节点。引擎与模型在第一次真正识别时才加载。"""

    def __init__(self, settings, store: BookStore, run_logger: RunLogger):
        self.settings = settings
        self.store = store
        self.runs = run_logger
        self._engine = None
        self._engine_error: Optional[str] = None
        self._lock = threading.Lock()
        self._infer_lock = threading.Lock()      # ONNX 会话串行使用，避免两个任务并发改引擎参数

    # ------------------------------------------------------------------ 状态
    def status(self) -> dict:
        """健康检查用：不加载模型，只报告依赖、源码和模型文件是否就绪。"""
        missing = [name for name in _REQUIRED_MODULES if importlib.util.find_spec(name) is None]
        vendor_ready = (VENDOR_ROOT / "rapidocr" / "__init__.py").is_file()
        model_dir = Path(self.settings.ocr_model_dir)
        model_files = sorted(p.name for p in model_dir.glob("*.onnx")) if model_dir.is_dir() else []
        return {
            "engine": self.settings.ocr_engine,
            "model_dir": str(model_dir),
            "auto": bool(self.settings.ocr_auto),
            "available": bool(vendor_ready and not missing),
            "vendor_ready": vendor_ready,
            "missing_dependencies": missing,
            "models_ready": len(model_files) >= 3,
            "model_files": model_files,
            "loaded": self._engine is not None,
            "error": self._engine_error,
        }

    # ------------------------------------------------------------------ 引擎
    def _load_engine(self):
        if self._engine is not None:
            return self._engine
        with self._lock:
            if self._engine is not None:
                return self._engine
            status = self.status()
            if not status["vendor_ready"]:
                raise OCRUnavailable("RapidOCR 源码未部署到 third_party/RapidOCR", details=status)
            if status["missing_dependencies"]:
                raise OCRUnavailable(
                    "缺少 OCR 依赖：" + "、".join(status["missing_dependencies"])
                    + "。请在项目根目录执行 .venv\\Scripts\\pip install -r requirements.txt",
                    details=status)
            try:
                from rapidocr import RapidOCR
            except Exception as exc:       # noqa: BLE001 - 第三方导入失败原因很多，统一转成可读错误
                self._engine_error = f"{type(exc).__name__}: {exc}"
                raise OCRUnavailable(f"RapidOCR 导入失败：{exc}", details=status) from exc
            params = {
                "Global.model_root_dir": str(self.settings.ocr_model_dir),
                "Global.text_score": float(self.settings.ocr_text_score),
                "Global.use_cls": bool(self.settings.ocr_use_cls),
                "Global.log_level": "warning",
            }
            try:
                self._engine = RapidOCR(params=params)
            except Exception as exc:       # noqa: BLE001
                self._engine_error = f"{type(exc).__name__}: {exc}"
                raise OCRUnavailable(
                    f"RapidOCR 初始化失败：{exc}",
                    details={**status, "hint": "确认模型文件完整，或运行 scripts/download_ocr_models.py"}) from exc
            self._engine_error = None
            return self._engine

    def _recognize_file(self, image_path: Path) -> tuple[dict, int]:
        engine = self._load_engine()
        started = time.perf_counter()
        try:
            with self._infer_lock:
                raw = engine(str(image_path))
        except Exception as exc:           # noqa: BLE001
            raise OCRFailed(f"RapidOCR 识别失败：{exc}") from exc
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return normalize_result(raw), elapsed_ms

    # ------------------------------------------------------------------ 单张 / 单页
    def recognize_image(self, image_path: Path, *, book_id: Optional[str] = None,
                        page_no: Optional[int] = None) -> dict:
        image_path = Path(image_path)
        if not image_path.is_file():
            raise OCRUnavailable("页面图片不存在，无法识别文字", details={"path": str(image_path)})
        with self.runs.start("ocr", {"book_id": book_id, "page_no": page_no,
                                     "image": image_path.name}) as run:
            run.set_model(_MODEL_LABEL, {"engine": self.settings.ocr_engine,
                                         "text_score": self.settings.ocr_text_score,
                                         "use_cls": bool(self.settings.ocr_use_cls)},
                          provider="rapidocr-local")
            output, elapsed_ms = self._recognize_file(image_path)
            run.add_output(image_path, role="input_image")
            run.note("result", {"full_text": output["full_text"], "block_count": len(output["blocks"]),
                                "char_count": len(output["full_text"].replace("\n", "")),
                                "elapsed_ms": elapsed_ms})
            run.set_cost({"billable": False, "amount": 0.0, "note": "本地推理"})
        return {**output, "run_id": run.run_id, "elapsed_ms": elapsed_ms}

    def recognize_page(self, book_id: str, page_no: int, force: bool = False) -> dict:
        book = self.store.load(book_id)
        page = BookStore.get_page(book, page_no)
        meta = page.get("ocr_meta") or {}
        if not force and meta.get("run_id"):
            text = (page.get("ocr_text") or {}).get("full_text", "")
            return {"book_id": book_id, "page_no": page_no, "cached": True, "run_id": meta.get("run_id"),
                    "full_text": text, "block_count": len((page.get("ocr_text") or {}).get("blocks") or []),
                    "char_count": len(text.replace("\n", "")), "elapsed_ms": meta.get("elapsed_ms")}

        image_path = self.store.resolve_media(book_id, page["page_image"])
        result = self.recognize_image(image_path, book_id=book_id, page_no=page_no)
        char_count = len(result["full_text"].replace("\n", ""))
        ocr_meta = {
            "run_id": result["run_id"],
            "engine": self.settings.ocr_engine,
            "model": _MODEL_LABEL,
            "text_score": float(self.settings.ocr_text_score),
            "use_cls": bool(self.settings.ocr_use_cls),
            "block_count": len(result["blocks"]),
            "char_count": char_count,
            "elapsed_ms": result["elapsed_ms"],
            "generated_at": now_iso(),
        }

        def mutate(current: dict) -> None:
            target = BookStore.get_page(current, page_no)
            target["ocr_text"] = {"full_text": result["full_text"], "blocks": result["blocks"]}
            target["ocr_meta"] = ocr_meta
            # 原文变了，之前的旁白审核结论作废，需要重新复核。
            if isinstance(target.get("safety_check"), dict):
                target["safety_check"]["status"] = "pending_review"

        self.store.update(book_id, mutate)
        return {"book_id": book_id, "page_no": page_no, "cached": False, "run_id": result["run_id"],
                "full_text": result["full_text"], "block_count": len(result["blocks"]),
                "char_count": char_count, "elapsed_ms": result["elapsed_ms"], "ocr_meta": ocr_meta}

    # ------------------------------------------------------------------ 整本
    def recognize_book(self, book_id: str, pages: Optional[list[int]] = None, force: bool = False,
                       progress: Optional[Callable[[float, str], None]] = None,
                       check_cancelled: Optional[Callable[[], None]] = None) -> dict:
        book = self.store.load(book_id)
        targets = [int(no) for no in (pages or [p["page_no"] for p in book.get("pages") or []])]
        if not targets:
            return {"book_id": book_id, "run_id": None, "page_count": 0, "succeeded": 0, "failed": 0,
                    "cached": 0, "pages": [], "errors": [], "model": _MODEL_LABEL}
        results: list[dict] = []
        errors: list[dict] = []
        first_error: Optional[Exception] = None
        total = len(targets)
        for index, page_no in enumerate(targets, start=1):
            if check_cancelled:
                check_cancelled()
            if progress:
                progress((index - 1) / total, f"第 {page_no} 页文字识别")
            try:
                results.append(self.recognize_page(book_id, page_no, force))
            except AppError as exc:
                errors.append({**exc.to_dict(), "page_no": page_no})
                first_error = first_error or exc
            except Exception as exc:       # noqa: BLE001
                errors.append({"code": "OCR_FAILED", "message": f"{type(exc).__name__}: {exc}",
                               "page_no": page_no})
                first_error = first_error or OCRFailed(f"第 {page_no} 页文字识别失败：{exc}")
        if progress:
            progress(1.0, "文字识别完成")
        if not results and first_error is not None:
            raise first_error
        cached = sum(1 for r in results if r.get("cached"))
        return {
            "book_id": book_id,
            "run_id": results[0].get("run_id") if results else None,
            "page_count": total,
            "succeeded": len(results),
            "failed": len(errors),
            "cached": cached,
            "model": _MODEL_LABEL,
            "pages": results,
            "errors": errors,
        }
