# -*- coding: utf-8 -*-
"""点击识物的“查表”主方案：按点击坐标在分页数据的物体框里查找。

真实接口和假接口共用这段逻辑，数据来源不同：
- 假接口用示例绘本 data/samples/demo_book；
- 真实接口用 data/books/<book_id>/book.json，物体框由 B 的内容理解模块写入。
  页面还没有物体框时返回 hit=false，并在 reason 里说明，不做猜测。

查表没有结果时的兜底（裁剪局部图交给 VLM 识别）属于 B 的模块，尚未接入。
"""
from __future__ import annotations

import time
import math
from typing import Optional

from .errors import ValidationError


def page_by_time(book: dict, play_time: float) -> dict:
    """按整本视频的播放时间找到对应的页。"""
    pages = book["pages"]
    for page in pages:
        rng = page.get("time_range")
        if rng and rng[0] <= play_time < rng[1]:
            return page
    return pages[-1] if play_time >= (book.get("video") or {}).get("duration", 0) else pages[0]


def identify(book: dict, page: dict, x: float, y: float, *, play_time: Optional[float] = None,
             coord_space: str = "image") -> dict:
    """返回接口约定 3.3 里的结果字段（不含 audio_url，由调用方补）。"""
    started = time.perf_counter()
    if not all(math.isfinite(v) for v in (x, y)) or (play_time is not None and (not math.isfinite(play_time) or play_time < 0)):
        raise ValidationError("坐标和播放时间必须为有效数值，播放时间不能为负数")
    if coord_space not in ("image", "normalized"):
        raise ValidationError("coord_space 必须是 image 或 normalized")
    frame = None
    if play_time is not None:
        page = page_by_time(book, play_time)
        frames = page.get("keyframes") or []
        if not frames:
            return {"hit": False, "object": None, "candidates": [],
                    "reason": "当前视频尚无关键帧标注，请切回图片识物", "source": "keyframe",
                    "page_no": page["page_no"], "keyframe_time": None, "frame_size": None,
                    "point": {"x": x, "y": y}, "timings": {"lookup_ms": 0, "tts_ms": 0}}
        if frames:
            frame = min(frames, key=lambda k: abs(k["time"] - play_time))
            objects, size, source = frame.get("objects") or [], frame["frame_size"], "keyframe"
    if frame is None:
        objects, size, source = page.get("objects") or [], page["image_size"], "page"

    if str(coord_space).lower() == "normalized":
        if not (0 <= x <= 1 and 0 <= y <= 1):
            raise ValidationError("归一化坐标应在 0 到 1 之间", details={"field": "x,y"})
        x, y = x * size["width"], y * size["height"]
    if not (0 <= x <= size["width"] and 0 <= y <= size["height"]):
        raise ValidationError(f"坐标超出画面范围（{size['width']}×{size['height']}）", code="POINT_OUT_OF_RANGE",
                              details={"field": "x,y"})

    hits = [o for o in objects if o["bbox"][0] <= x <= o["bbox"][2] and o["bbox"][1] <= y <= o["bbox"][3]]
    hits.sort(key=lambda o: (o["bbox"][2] - o["bbox"][0]) * (o["bbox"][3] - o["bbox"][1]))   # 重叠时取最小的框
    best = hits[0] if hits else None
    reason = None
    if not best:
        reason = ("这一页还没有物体标注（内容理解模块尚未处理）" if not objects
                  else "点击位置没有标注的物体")
    return {
        "hit": bool(best),
        "object": {"name": best["name"], "bbox": best["bbox"], "explanation": best.get("explanation", "")}
        if best else None,
        "candidates": [o["name"] for o in hits],
        "reason": reason,
        "source": source, "page_no": page["page_no"],
        "keyframe_time": frame["time"] if frame else None,
        "frame_size": size, "point": {"x": round(x, 1), "y": round(y, 1)},
        "timings": {"lookup_ms": int((time.perf_counter() - started) * 1000), "tts_ms": 0},
    }
