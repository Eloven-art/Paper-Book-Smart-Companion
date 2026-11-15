# -*- coding: utf-8 -*-
"""VLM 内容理解节点：把页面图片交给视觉语言模型，写回画面描述和可点击对象框。

第一版只依赖同学 VLM 已经确认的能力：
- 必要：页码、画面描述、对象名称 + bbox；
- 可选：contours、情节、置信度。
没有真实 contours 就只写 bbox，阅读页显示矩形提示，**绝不根据 bbox 伪造轮廓**。

约定输出（内部归一化后）：
    page["scene_summary"] = "..."
    page["objects"] = [{"object_id","name","bbox":[x1,y1,x2,y2],"explanation","confidence"?,"contours"?}]
    page["plot_events"] = ["..."]

VLM 返回的坐标按“发送给模型的图片尺寸”归一化回原始页图尺寸，保证 bbox 始终是
原图像素坐标，与阅读页、点击识物使用同一坐标系。
"""
from __future__ import annotations

import base64
import io
import json
import math
import re
from pathlib import Path
from string import Template
from typing import Callable, Optional

from PIL import Image

from configs.settings import Settings
from server.book_store import BookStore
from server.errors import VLMFailed, VLMUnavailable
from server.run_logger import RunLogger, now_iso, sha256_file

from .story_agent import load_prompt
from .vlm_client import VLMClient

PROMPT_NAME = "vlm_v1"
_MAX_OBJECTS = 12


def encode_image(image_path: Path, max_side: int = 1280) -> tuple[str, tuple[int, int]]:
    """把页图缩到 max_side 内并转 data URI；返回 (uri, 发送尺寸)。"""
    with Image.open(image_path) as image:
        image = image.convert("RGB")
        scale = min(1.0, max_side / max(image.size))
        if scale < 1.0:
            image = image.resize((max(1, int(image.width * scale)), max(1, int(image.height * scale))),
                                 Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=90)
        return ("data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii"),
                (image.width, image.height))


def extract_json(raw: str) -> dict:
    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise VLMFailed("VLM 没有返回 JSON 对象", details={"raw": text[:500]})
    try:
        payload = json.loads(text[start:end + 1])
    except ValueError as exc:
        raise VLMFailed(f"VLM 返回的 JSON 无法解析：{exc}", details={"raw": text[:500]}) from exc
    if not isinstance(payload, dict):
        raise VLMFailed("VLM 返回的 JSON 顶层不是对象", details={"raw": text[:500]})
    return payload


def _number(value) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _bbox(value, bbox_format: str = "xyxy") -> Optional[tuple[float, float, float, float]]:
    """接受 [x1,y1,x2,y2]、[x,y,w,h]、{x1,y1,x2,y2}、{x,y,width,height} 或 4 个点。

    bbox_format 只影响“4 个数字的数组”：xyxy（默认，接口约定）或 xywh。
    dict 和点集可以自行判断结构，不受它影响。
    """
    if value is None:
        return None
    if isinstance(value, dict):
        if all(k in value for k in ("x1", "y1", "x2", "y2")):
            box = [value["x1"], value["y1"], value["x2"], value["y2"]]
        elif all(k in value for k in ("left", "top", "right", "bottom")):
            box = [value["left"], value["top"], value["right"], value["bottom"]]
        elif all(k in value for k in ("x", "y", "width", "height")):
            x, y, w, h = value["x"], value["y"], value["width"], value["height"]
            box = [x, y, float(x) + float(w), float(y) + float(h)]
        elif all(k in value for k in ("x", "y", "w", "h")):
            x, y, w, h = value["x"], value["y"], value["w"], value["h"]
            box = [x, y, float(x) + float(w), float(y) + float(h)]
        else:
            return None
        numbers = [_number(v) for v in box]
    elif isinstance(value, (list, tuple)):
        if len(value) == 4 and all(isinstance(v, (int, float)) for v in value):
            numbers = [_number(v) for v in value]
            if None in numbers:
                return None
            if bbox_format == "xywh":
                x, y, w, h = numbers
                numbers = [x, y, x + w, y + h]
        elif len(value) == 8 and all(isinstance(v, (int, float)) for v in value):
            nums = [_number(v) for v in value]
            if None in nums:
                return None
            numbers = [min(nums[0::2]), min(nums[1::2]), max(nums[0::2]), max(nums[1::2])]
        else:
            points = []
            for point in value:
                if isinstance(point, (list, tuple)) and len(point) >= 2:
                    px, py = _number(point[0]), _number(point[1])
                    if px is not None and py is not None:
                        points.append((px, py))
            if len(points) < 2:
                return None
            numbers = [min(p[0] for p in points), min(p[1] for p in points),
                       max(p[0] for p in points), max(p[1] for p in points)]
    else:
        return None
    if None in numbers:
        return None
    x1, y1, x2, y2 = numbers
    return x1, y1, x2, y2


def _contours(value, sent_size: tuple[int, int], original_size: tuple[int, int],
              bbox_format: str = "xyxy"):
    """只接受模型真实给出的轮廓；无效就返回空列表，不用 bbox 伪造。"""
    if not isinstance(value, (list, tuple)) or not value:
        return []
    sw, sh = sent_size
    ow, oh = original_size
    polygons = value
    if value and isinstance(value[0], (int, float)):
        return []
    if value and isinstance(value[0], (list, tuple)) and value[0] and isinstance(value[0][0], (int, float)):
        polygons = [value]       # 单个多边形
    result = []
    for polygon in polygons:
        if not isinstance(polygon, (list, tuple)) or len(polygon) < 3:
            continue
        points = []
        for point in polygon:
            if not isinstance(point, (list, tuple)) or len(point) < 2:
                continue
            x, y = _number(point[0]), _number(point[1])
            if x is None or y is None:
                continue
            if bbox_format == "xyxy_1000":
                x, y = x * sw / 1000, y * sh / 1000
            elif bbox_format == "xyxy_normalized":
                x, y = x * sw, y * sh
            x = min(max(x * ow / sw, 0), ow)
            y = min(max(y * oh / sh, 0), oh)
            points.append([int(round(x)), int(round(y))])
        if len(points) >= 3:
            result.append(points)
    return result


def normalize_vlm_result(payload: dict, *, expected_page_no: int,
                         sent_size: tuple[int, int], original_size: tuple[int, int],
                         bbox_format: str = "xyxy") -> dict:
    warnings: list[str] = []
    raw_page = payload.get("page_no", payload.get("page"))
    page_no = _number(raw_page)
    if page_no is None:
        page_no = expected_page_no
        warnings.append("page_no_missing")
    elif int(page_no) != int(expected_page_no):
        raise VLMFailed(f"VLM 返回页码 {int(page_no)} 与请求页码 {expected_page_no} 不一致",
                        details={"page_no": int(page_no), "expected": expected_page_no})

    scene = ""
    for key in ("scene_summary", "description", "scene", "summary"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            scene = value.strip()
            break
    if not scene:
        warnings.append("scene_summary_missing")

    raw_objects = payload.get("objects", payload.get("items", payload.get("object_list")))
    if isinstance(raw_objects, dict):
        # 同学的 gemini_analyze.py 格式：{"名称<English>": [x1,y1,x2,y2], ...}
        raw_objects = [{"name": key, "bbox": value} for key, value in raw_objects.items()]
    if raw_objects is None:
        # 兼容直接返回对象字典（没有 objects 包装）的格式。
        candidate = {k: v for k, v in payload.items()
                     if isinstance(v, (list, tuple, dict)) and _bbox(v, bbox_format) is not None}
        raw_objects = [{"name": key, "bbox": value} for key, value in candidate.items()]
    if not isinstance(raw_objects, (list, tuple)):
        raise VLMFailed("VLM 的 objects 不是数组或对象字典", details={"objects": str(raw_objects)[:300]})

    sw, sh = sent_size
    ow, oh = original_size
    objects, dropped, invalid_contours = [], 0, 0
    for item in raw_objects:
        if not isinstance(item, dict):
            dropped += 1
            continue
        name = ""
        for key in ("name", "label", "object_name", "class"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                name = re.sub(r"<[^>]*>", "", value).strip()
                break
        if not name:
            dropped += 1
            continue
        raw_box = item.get("bbox", item.get("box"))
        box = _bbox(raw_box, bbox_format)
        if box is None:
            dropped += 1
            continue
        x1, y1, x2, y2 = box
        if bbox_format == "xyxy_1000":
            x1, y1, x2, y2 = x1 * sw / 1000, y1 * sh / 1000, x2 * sw / 1000, y2 * sh / 1000
        elif bbox_format == "xyxy_normalized":
            x1, y1, x2, y2 = x1 * sw, y1 * sh, x2 * sw, y2 * sh
        x1, y1 = x1 * ow / sw, y1 * oh / sh
        x2, y2 = x2 * ow / sw, y2 * oh / sh
        x1, y1 = min(max(x1, 0), ow), min(max(y1, 0), oh)
        x2, y2 = min(max(x2, 0), ow), min(max(y2, 0), oh)
        if x2 - x1 < 2 or y2 - y1 < 2:
            dropped += 1
            continue
        obj = {
            "object_id": f"p{expected_page_no}_o{len(objects) + 1}",
            "name": name,
            "bbox": [int(round(x1)), int(round(y1)), int(round(x2)), int(round(y2))],
            "explanation": str(item.get("explanation") or item.get("description") or "").strip()[:300],
        }
        confidence = _number(item.get("confidence", item.get("score")))
        if confidence is not None:
            obj["confidence"] = round(min(max(confidence, 0.0), 1.0), 4)
        contours = _contours(item.get("contours"), sent_size, original_size, bbox_format)
        if item.get("contours") and not contours:
            invalid_contours += 1
        if contours:
            obj["contours"] = contours
        objects.append(obj)
    if dropped:
        warnings.append(f"dropped_objects:{dropped}")
    if invalid_contours:
        warnings.append(f"invalid_contours:{invalid_contours}")
    if not objects:
        warnings.append("objects_empty")

    raw_events = payload.get("plot_events", payload.get("plot", payload.get("events")))
    if isinstance(raw_events, str):
        raw_events = [raw_events]
    events = [str(x).strip() for x in raw_events if isinstance(x, str) and x.strip()][:10] if isinstance(raw_events, list) else []

    return {"page_no": int(expected_page_no), "scene_summary": scene, "objects": objects,
            "plot_events": events, "warnings": warnings}


class VLMAgent:
    def __init__(self, settings: Settings, store: BookStore, run_logger: RunLogger,
                 client: Optional[VLMClient] = None):
        self.settings = settings
        self.store = store
        self.runs = run_logger
        self._client = client
        self.prompt = load_prompt(settings.prompts_dir, PROMPT_NAME)

    def status(self) -> dict:
        configured = bool(self.settings.vlm_url)
        return {"configured": bool(self._client is not None or configured),
                "available": bool(self._client is not None or configured),
                "url_configured": configured,
                "model": self.settings.vlm_model,
                "protocol": self.settings.vlm_protocol,
                "auto": self.settings.vlm_auto,
                "prompt_version": self.prompt.version}

    def _get_client(self) -> VLMClient:
        if self._client is None:
            self._client = VLMClient(
                self.settings.vlm_url, self.settings.vlm_api_key, self.settings.vlm_model,
                protocol=self.settings.vlm_protocol, timeout=self.settings.vlm_timeout,
                temperature=self.settings.vlm_temperature)
        return self._client

    def analyze_page(self, book_id: str, page_no: int, force: bool = False) -> dict:
        book = self.store.load(book_id)
        page = BookStore.get_page(book, page_no)
        image_path = self.store.resolve_media(book_id, page["page_image"])
        image_sha = sha256_file(image_path)
        meta = page.get("vlm_meta") or {}
        if not force and meta.get("run_id") and meta.get("image_sha256") == image_sha:
            return {"page_no": page_no, "cached": True, "run_id": meta.get("run_id"),
                    "object_count": len(page.get("objects") or []),
                    "warnings": meta.get("warnings") or []}

        data_uri, sent_size = encode_image(image_path, self.settings.vlm_max_image_side)
        original_size = (int(page["image_size"]["width"]), int(page["image_size"]["height"]))
        values = {
            "page_no": page_no, "page_count": len(book.get("pages") or []),
            "book_title": book.get("title") or "",
            "width": sent_size[0], "height": sent_size[1],
            "ocr_text": (page.get("ocr_text") or {}).get("full_text") or "（本页没有文字）",
        }
        system_prompt = Template(self.prompt.sections.get("system", "")).safe_substitute(values)
        user_prompt = Template(self.prompt.sections.get("user", "")).safe_substitute(values)

        with self.runs.start("vlm", {"book_id": book_id, "page_no": page_no,
                                     "image": page["page_image"], "image_sha256": image_sha,
                                     "sent_size": sent_size, "original_size": original_size}) as run:
            run.set_prompt(self.prompt.version, self.prompt.sha256, rendered=user_prompt)
            run.set_model(self.settings.vlm_model,
                          {"protocol": self.settings.vlm_protocol, "temperature": self.settings.vlm_temperature,
                           "max_image_side": self.settings.vlm_max_image_side}, provider="vlm_http")
            run.add_output(image_path, role="input_image")
            result = self._get_client().analyze(
                data_uri, system_prompt, user_prompt, page_no=page_no,
                image_size={"width": sent_size[0], "height": sent_size[1]})
            normalized = normalize_vlm_result(
                extract_json(result.text), expected_page_no=page_no,
                sent_size=sent_size, original_size=original_size,
                bbox_format=self.settings.vlm_bbox_format)
            meta = {
                "run_id": run.run_id, "provider": "vlm_http",
                "protocol": self.settings.vlm_protocol, "model": result.model or self.settings.vlm_model,
                "image_sha256": image_sha, "sent_size": {"width": sent_size[0], "height": sent_size[1]},
                "object_count": len(normalized["objects"]), "warnings": normalized["warnings"],
                "generated_at": now_iso(), "elapsed_ms": result.elapsed_ms,
            }

            def mutate(current: dict) -> None:
                target = BookStore.get_page(current, page_no)
                target["scene_summary"] = normalized["scene_summary"]
                target["objects"] = normalized["objects"]
                if normalized["plot_events"]:
                    target["plot_events"] = normalized["plot_events"]
                target["vlm_meta"] = meta
                if isinstance(target.get("safety_check"), dict):
                    target["safety_check"]["status"] = "pending_review"

            self.store.update(book_id, mutate)
            run.note("result", {"page_no": page_no, "scene_summary": normalized["scene_summary"],
                                "object_count": len(normalized["objects"]),
                                "warnings": normalized["warnings"]})
            run.note("raw_output", result.text[:8000])
            run.set_cost({"billable": False, "amount": 0.0, "note": "VLM 服务（本地/外部）"})
        return {"page_no": page_no, "cached": False, "run_id": run.run_id,
                "scene_summary": normalized["scene_summary"], "objects": normalized["objects"],
                "plot_events": normalized["plot_events"], "warnings": normalized["warnings"],
                "object_count": len(normalized["objects"]), "elapsed_ms": result.elapsed_ms}

    def analyze_book(self, book_id: str, pages: Optional[list[int]] = None, force: bool = False,
                     progress: Optional[Callable[[float, str], None]] = None,
                     check_cancelled: Optional[Callable[[], None]] = None) -> dict:
        book = self.store.load(book_id)
        targets = [int(no) for no in (pages or [p["page_no"] for p in book.get("pages") or []])]
        results, errors = [], []
        first_error = None
        total = len(targets)
        for index, page_no in enumerate(targets, start=1):
            if check_cancelled:
                check_cancelled()
            if progress:
                progress((index - 1) / total, f"第 {page_no} 页画面理解")
            try:
                results.append(self.analyze_page(book_id, page_no, force))
            except Exception as exc:       # noqa: BLE001 - 单页失败不终止整本
                errors.append({"page_no": page_no, "code": getattr(exc, "code", "VLM_FAILED"),
                               "message": str(exc)})
                first_error = first_error or exc
        if progress:
            progress(1.0, "画面理解完成")
        if not results and first_error is not None:
            raise first_error
        cached = sum(1 for r in results if r.get("cached"))
        return {"book_id": book_id, "run_id": results[0].get("run_id") if results else None,
                "page_count": total, "succeeded": len(results), "failed": len(errors),
                "cached": cached, "pages": results, "errors": errors}
