# -*- coding: utf-8 -*-
"""画面理解节点：把绘本页图交给 VLM，把画面结构化成接口约定的分页字段。

做三件事：
1. 读页图，调 VlmClient（OpenAI 兼容多模态接口）；
2. 解析模型返回的 JSON，并把 bbox 统一成“图片像素坐标、且一定落在画面内”
   —— 模型经常越界，也会给 0-1 的坐标系，这里统一收口；
3. 写回 book.json 的 ocr_text / objects / scene_summary / plot_events / characters，
   并记 understanding_meta（模型、用量、提示词版本、修正了几个框）。

设计边界：
- VLM 未配置时不干活，由调用方（/books 上传）判断 configured 后决定是否排任务；
- 已经理解过的页面默认跳过，force=True 才重跑（VLM 免费但有并发限制，别重复打）；
- 单页失败不拖垮整本：失败的页记进 failed，其余继续；全失败才抛错让任务显示失败。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable, Optional

from configs.settings import Settings
from server.book_store import BookStore
from server.errors import AppError, ExternalServiceError, NotFoundError
from server.retry import call_with_retry
from server.run_logger import RunLogger, now_iso, sha256_text

from .vlm_client import VlmClient

PROMPT_NAME = "understanding_v1"
SYSTEM = "你是儿童绘本的画面理解助手。只输出 JSON，不输出任何解释文字。"
MIN_BOX_SIDE = 4                      # 宽或高小于这个像素数的框视为无效
MAX_BOX_RATIO = 0.92                  # 占画面 92% 以上的框当成“整页”，不参与识物
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
_TRAILING_COMMA = re.compile(r",(\s*[}\]])")


# ---------------------------------------------------------------- 解析与坐标
def parse_json(raw: str) -> dict:
    """从模型回复里取出 JSON 对象；带 ``` 包裹、前后有废话、尾逗号都能救回来。"""
    text = _FENCE.sub("", (raw or "").strip())
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ExternalServiceError("画面理解没有返回 JSON", service="vlm", retryable=True,
                                   details={"text": text[:200]})
    body = text[start:end + 1]
    try:
        data = json.loads(body)
    except ValueError:
        # 模型最常见的两种手误：数组/对象最后一项后面多一个逗号；以及全角逗号
        repaired = _TRAILING_COMMA.sub(r"\1", body.replace("，", ","))
        try:
            data = json.loads(repaired)
        except ValueError as exc:
            raise ExternalServiceError("画面理解的 JSON 解析失败", service="vlm", retryable=True,
                                       details={"text": body[:200]}) from exc
    if not isinstance(data, dict):
        raise ExternalServiceError("画面理解返回的不是 JSON 对象", service="vlm", retryable=True)
    return data


def normalize_bbox(raw, width: int, height: int) -> Optional[list[int]]:
    """把框统一成“图片像素 + 画面内”，无效返回 None。

    只做两件事：把 0-1 归一化的框换算成像素；把越界的坐标裁回画面内。
    不猜 0-1000 栅格——1280x720 的图上，正常的像素框数值也全都小于 1000，
    猜错会悄悄把框缩到 0.78 倍，比直接越界更难查。提示词里已经明确要求像素坐标；
    真遇到只给 0-1000 的服务，应该按模型单独配置换算，而不是在这里赌。

    裁剪后宽或高不足 MIN_BOX_SIDE 的，认为模型没真框住东西，丢弃。
    """
    try:
        values = [float(v) for v in raw]
    except (TypeError, ValueError):
        return None
    if len(values) != 4 or width <= 0 or height <= 0:
        return None
    x1, y1, x2, y2 = values
    if max(values) <= 1.5:                               # 0-1 归一化
        x1, x2 = x1 * width, x2 * width
        y1, y2 = y1 * height, y2 * height
    x1, x2 = sorted((x1, x2))
    y1, y2 = sorted((y1, y2))
    x1, x2 = min(max(x1, 0.0), float(width)), min(max(x2, 0.0), float(width))
    y1, y2 = min(max(y1, 0.0), float(height)), min(max(y2, 0.0), float(height))
    if x2 - x1 < MIN_BOX_SIDE or y2 - y1 < MIN_BOX_SIDE:
        return None
    return [round(x1), round(y1), round(x2), round(y2)]


def normalize_objects(raw, width: int, height: int) -> tuple[list[dict], int]:
    """整理 objects，返回 (可用列表, 被修正或丢弃的个数)。"""
    if not isinstance(raw, list):
        return [], 0
    objects: list[dict] = []
    adjusted = 0
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            adjusted += 1
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            adjusted += 1
            continue
        bbox = normalize_bbox(item.get("bbox"), width, height)
        if bbox is None:
            adjusted += 1
            continue
        # 整页大小的框（“背景”“画面”那类）会把点击空白处也判成命中，直接丢掉
        if (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]) >= MAX_BOX_RATIO * width * height:
            adjusted += 1
            continue
        try:
            if [round(float(v)) for v in item.get("bbox")] != bbox:
                adjusted += 1
        except (TypeError, ValueError):
            adjusted += 1
        objects.append({
            "object_id": str(item.get("object_id") or f"obj-{index:02d}"),
            "name": name,
            "bbox": bbox,
            "explanation": str(item.get("explanation") or "").strip(),
        })
    return objects, adjusted


def normalize_ocr(raw, width: int, height: int) -> dict:
    """整理 ocr_text：文字块保留，框不可用的块只留文字。"""
    empty = {"full_text": "", "blocks": []}
    if not isinstance(raw, dict):
        return empty
    blocks: list[dict] = []
    for item in raw.get("blocks") or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        block = {"text": text}
        bbox = normalize_bbox(item.get("bbox"), width, height)
        if bbox:
            block["bbox"] = bbox
        blocks.append(block)
    return {"full_text": str(raw.get("full_text") or "").strip(), "blocks": blocks}


def normalize_characters(raw) -> list[dict]:
    if not isinstance(raw, list):
        return []
    characters = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        characters.append({"character_id": str(item.get("character_id") or name),
                           "name": name,
                           "appearance": str(item.get("appearance") or "").strip()})
    return characters


def normalize_events(raw) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


# ---------------------------------------------------------------- 节点
class UnderstandingAgent:
    """一页一次 VLM 调用；结果写回分页数据，供识物、问答、旁白共用。"""

    def __init__(self, settings: Settings, store: BookStore, runs: RunLogger, vlm: VlmClient):
        self.settings, self.store, self.runs, self.vlm = settings, store, runs, vlm

    @property
    def configured(self) -> bool:
        return self.vlm.configured

    def _prompt(self) -> tuple[str, str]:
        path = Path(self.settings.prompts_dir) / f"{PROMPT_NAME}.txt"
        text = path.read_text(encoding="utf-8")
        return text, sha256_text(text)

    def _image_size(self, page: dict, image: Path) -> tuple[int, int]:
        size = page.get("image_size") or {}
        width, height = int(size.get("width") or 0), int(size.get("height") or 0)
        if width > 0 and height > 0:
            return width, height
        from PIL import Image                      # 兜底：分页数据没写尺寸时按真实图片
        with Image.open(image) as im:
            return int(im.width), int(im.height)

    def understand_page(self, book_id: str, page_no: int, force: bool = False) -> dict:
        """理解一页并写回；已经理解过且没 force 时直接跳过。"""
        book = self.store.load(book_id)
        page = BookStore.get_page(book, page_no)
        if page.get("understanding_meta") and not force:
            return {"book_id": book_id, "page_no": page_no, "skipped": True,
                    "objects": len(page.get("objects") or [])}
        image = self.store.book_dir(book_id) / page["page_image"]
        if not image.is_file():
            raise NotFoundError(f"绘本 {book_id} 第 {page_no} 页的图片不见了",
                                code="PAGE_IMAGE_MISSING",
                                details={"book_id": book_id, "page_no": page_no,
                                         "page_image": page["page_image"]})
        width, height = self._image_size(page, image)
        prompt, prompt_sha = self._prompt()

        with self.runs.start("understanding", input={"book_id": book_id, "page_no": page_no,
                                                     "page_image": page["page_image"]}) as run:
            run.set_prompt(PROMPT_NAME, prompt_sha, rendered=prompt)
            run.set_model(self.vlm.model, {"temperature": 0.1})
            seen: dict = {}

            def attempt():
                """一次“调用 + 解析”。解析失败也走重试：模型偶尔会吐不合法的 JSON。"""
                outcome = self.vlm.analyze(image, prompt, system=SYSTEM, temperature=0.1)
                seen["text"] = outcome.text
                return parse_json(outcome.text), outcome

            try:
                # 免费视觉模型在高峰期会连续 1305，等得比普通接口久一些：约 2+4+8 秒
                data, result = call_with_retry(
                    attempt, attempts=max(4, self.settings.retry_attempts),
                    base_delay=max(2.0, self.settings.retry_base_delay * 2),
                    label=f"画面理解 p{page_no}")
            except AppError:
                run.note("raw_head", (seen.get("text") or "")[:400])   # 失败也留一份现场，方便查
                raise

            objects, adjusted = normalize_objects(data.get("objects"), width, height)
            ocr = normalize_ocr(data.get("ocr_text"), width, height)
            meta = {
                "run_id": run.run_id,
                "model": result.model,
                "prompt_version": PROMPT_NAME,
                "prompt_sha256": prompt_sha,
                "usage": result.usage,
                "elapsed_ms": result.elapsed_ms,
                "bbox_adjusted": adjusted,
                "at": now_iso(),
            }
            scene_summary = str(data.get("scene_summary") or "").strip()
            plot_events = normalize_events(data.get("plot_events"))
            characters = normalize_characters(data.get("characters"))

            def mutate(current: dict) -> None:
                target = BookStore.get_page(current, page_no)
                target["ocr_text"] = ocr
                target["objects"] = objects
                target["scene_summary"] = scene_summary
                target["plot_events"] = plot_events
                if characters:
                    target["characters"] = characters
                if scene_summary:
                    target["image_size"] = target.get("image_size") or {"width": width, "height": height}
                target["understanding_meta"] = meta

            self.store.update(book_id, mutate)
            run.note("objects", len(objects))
            run.note("bbox_adjusted", adjusted)

        return {"book_id": book_id, "page_no": page_no, "skipped": False, "run_id": meta["run_id"],
                "objects": len(objects), "ocr_chars": len(ocr["full_text"]),
                "bbox_adjusted": adjusted, "usage": result.usage}

    def understand_book(self, book_id: str, pages: Optional[list[int]] = None, force: bool = False,
                        progress: Optional[Callable[[float, Optional[str]], None]] = None,
                        check_cancelled: Optional[Callable[[], None]] = None) -> dict:
        """逐页理解整本（或指定几页）。单页失败不中断，最后汇总。"""
        book = self.store.load(book_id)
        targets = [int(p["page_no"]) for p in book.get("pages") or []]
        if pages:
            wanted = set(pages)
            targets = [n for n in targets if n in wanted]
        if not targets:
            return {"book_id": book_id, "pages": 0, "done": [], "skipped": [], "failed": []}

        done: list[int] = []
        skipped: list[int] = []
        failed: list[dict] = []
        first_error: Optional[AppError] = None
        total = len(targets)
        for index, page_no in enumerate(targets, start=1):
            if check_cancelled:
                check_cancelled()
            if progress:
                progress((index - 1) / total, f"第 {page_no} 页：读画面")
            try:
                outcome = self.understand_page(book_id, page_no, force=force)
            except AppError as exc:
                failed.append({"page_no": page_no, "code": exc.code, "message": exc.message})
                first_error = first_error or exc
                continue
            (skipped if outcome.get("skipped") else done).append(page_no)
        if progress:
            progress(1.0, "画面理解完成")
        if failed and not done and not skipped and first_error is not None:
            raise first_error                     # 整本一页都没成，让任务如实显示失败
        return {"book_id": book_id, "pages": total, "done": done, "skipped": skipped, "failed": failed}
