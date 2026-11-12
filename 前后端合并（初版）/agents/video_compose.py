# -*- coding: utf-8 -*-
"""视频合成（MoviePy 2.x）：片段适配画布、配音、字幕、页间转场、整本拼接。

约定：
- 画布固定为配置里的宽高（默认 1280×720）。片段比例不同就等比缩放后居中，四周用纸色填充，
  不裁剪画面，保证原绘本插图完整可见。
- 每页时长 = max(最短页时长, 旁白时长 + 尾部停留)。图生视频片段不够长时先最多放慢到 1.25 倍，
  仍不够就用最后一帧定格补足；片段过长则截断。
- 字幕按句切分，每条一行，显示时长按字数占比分配到旁白时长上。
- 页间用交叉淡入转场。每页在整本视频里的 time_range 取 [本页开始, 下一页开始)，
  相邻页首尾相接、互不重叠，方便按播放时间反查页码。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image, ImageDraw, ImageFont

SENTENCE_END = "。！？!?；;…"
PAUSE_MARKS = "，、,：:"
CLOSERS = "”’」』）)》"


# ---------------------------------------------------------------- 基础工具
def probe_duration(path: Path) -> float:
    from moviepy import VideoFileClip
    with VideoFileClip(str(path), audio=False) as clip:
        return round(float(clip.duration), 3)


def fit_image(image_path: Path, size: tuple[int, int], background: tuple) -> np.ndarray:
    """把图片等比缩放后居中放到画布上，返回 RGB 数组。"""
    width, height = size
    with Image.open(image_path) as img:
        img = img.convert("RGB")
        scale = min(width / img.width, height / img.height)
        new = (max(1, round(img.width * scale)), max(1, round(img.height * scale)))
        img = img.resize(new, Image.Resampling.LANCZOS)
        canvas = Image.new("RGB", size, tuple(background))
        canvas.paste(img, ((width - new[0]) // 2, (height - new[1]) // 2))
        return np.asarray(canvas)


def _write(clip, out_path: Path, fps: int, with_audio: bool) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_audio = out_path.parent / f".tmp_audio_{uuid.uuid4().hex[:8]}.m4a"
    clip.write_videofile(str(out_path), fps=fps, codec="libx264", audio=with_audio, audio_codec="aac",
                         preset="medium", pixel_format="yuv420p", temp_audiofile=str(tmp_audio),
                         remove_temp=True, logger=None, ffmpeg_params=["-movflags", "+faststart"])
    tmp_audio.unlink(missing_ok=True)


def render_kenburns(image_path: Path, out_path: Path, seconds: float, size: tuple[int, int], fps: int,
                    zoom: float, background: tuple) -> None:
    """本地降级片段：静态页图从 1.0 倍匀速推近到 (1+zoom) 倍，以画面中心为原点。"""
    from moviepy import VideoClip
    width, height = size
    base = Image.fromarray(fit_image(image_path, size, background))

    def frame(t: float) -> np.ndarray:
        z = 1.0 + zoom * min(1.0, max(0.0, t / seconds))
        cw, ch = width / z, height / z
        box = ((width - cw) / 2, (height - ch) / 2, (width + cw) / 2, (height + ch) / 2)
        return np.asarray(base.resize(size, Image.Resampling.BILINEAR, box=box))

    clip = VideoClip(frame_function=frame, duration=seconds)
    try:
        _write(clip, out_path, fps, with_audio=False)
    finally:
        clip.close()


# ---------------------------------------------------------------- 字幕
def split_subtitles(narration: str, max_chars: int = 20) -> list[str]:
    """把旁白切成字幕条：先按句末标点分句，过长的句子再按逗号分，仍过长就按字数硬切。"""
    text = "".join((narration or "").split())
    sentences, buf = [], ""
    for i, ch in enumerate(text):
        buf += ch
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if ch in SENTENCE_END and nxt not in SENTENCE_END + CLOSERS:
            sentences.append(buf)
            buf = ""
        elif ch in CLOSERS and len(buf) > 1 and buf[-2] in SENTENCE_END:
            sentences.append(buf)
            buf = ""
    if buf:
        sentences.append(buf)

    units: list[str] = []
    for sentence in sentences:
        if len(sentence) <= max_chars:
            units.append(sentence)
            continue
        pieces, piece = [], ""
        for ch in sentence:
            piece += ch
            if ch in PAUSE_MARKS:
                pieces.append(piece)
                piece = ""
        if piece:
            pieces.append(piece)
        line = ""
        for p in pieces:
            if line and len(line) + len(p) > max_chars:
                units.append(line)
                line = ""
            line += p
            while len(line) > max_chars:
                units.append(line[:max_chars])
                line = line[max_chars:]
        if line:
            units.append(line)
    return [u for u in units if u.strip()]


def _weight(text: str) -> float:
    spoken = sum(1 for ch in text if ch not in SENTENCE_END + PAUSE_MARKS + CLOSERS + "“‘「『（(《")
    return max(1.0, spoken + 0.6 * sum(1 for ch in text if ch in SENTENCE_END))


def schedule_subtitles(units: list[str], speech_seconds: float) -> list[dict]:
    """按字数占比把字幕条排到旁白时长上，返回 [{"start", "end", "text"}]。"""
    if not units or speech_seconds <= 0:
        return []
    total = sum(_weight(u) for u in units)
    cues, cursor = [], 0.0
    for u in units:
        span = speech_seconds * _weight(u) / total
        cues.append({"start": round(cursor, 3), "end": round(cursor + span, 3), "text": u})
        cursor += span
    return cues


def render_subtitle(text: str, canvas: tuple[int, int], font_path: str) -> np.ndarray:
    """渲染一条字幕为 RGBA 图：半透明深色圆角底 + 白字描边。"""
    width, height = canvas
    font_size = max(18, round(height * 0.052))
    font = ImageFont.truetype(font_path, font_size)
    probe = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
    left, top, right, bottom = probe.textbbox((0, 0), text, font=font, stroke_width=2)
    pad_x, pad_y = round(font_size * 0.7), round(font_size * 0.38)
    w, h = right - left + 2 * pad_x, bottom - top + 2 * pad_y
    img = Image.new("RGBA", (min(w, width), h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((0, 0, img.width - 1, h - 1), radius=round(h * 0.35), fill=(20, 22, 30, 165))
    draw.text((pad_x - left, pad_y - top), text, font=font, fill=(255, 255, 255, 255),
              stroke_width=2, stroke_fill=(20, 22, 30, 255))
    return np.asarray(img)


# ---------------------------------------------------------------- 单页与整本
@dataclass
class PageClipInfo:
    path: Path
    duration: float
    speech_seconds: float
    raw_clip_seconds: float
    slowdown: float
    freeze_seconds: float
    subtitles: list


def plan_timing(raw_seconds: float, speech_seconds: float, *, tail_padding: float, min_page_seconds: float,
                max_slowdown: float) -> dict:
    """算出一页的时长，以及片段不够长时“放慢多少、定格多久”。"""
    page_seconds = round(max(min_page_seconds, speech_seconds + tail_padding), 3)
    slowdown, freeze = 1.0, 0.0
    if raw_seconds < page_seconds:
        slowdown = min(max_slowdown, page_seconds / raw_seconds)
        if slowdown <= 1.01:
            slowdown = 1.0
        freeze = max(0.0, page_seconds - raw_seconds * slowdown)
    return {"page_seconds": page_seconds, "slowdown": round(slowdown, 3), "freeze_seconds": round(freeze, 3)}


def compose_page(raw_clip_path: Path, audio_path: Optional[Path], narration: str, out_path: Path, *,
                 size: tuple[int, int], fps: int, background: tuple, tail_padding: float,
                 min_page_seconds: float, max_slowdown: float, font_path: Optional[str],
                 subtitle_max_chars: int) -> PageClipInfo:
    """合成一页：片段适配画布并补足时长 + 配音 + 字幕，写出 mp4。

    逐帧自己算画面（取源帧 → 需要时缩放居中 → 只在字幕所在的小矩形里做半透明叠加），
    比通用的图层合成快很多；解码、编码、混音仍由 MoviePy 完成。
    """
    from moviepy import AudioFileClip, VideoClip, VideoFileClip
    width, height = size
    opened = []
    try:
        raw = VideoFileClip(str(raw_clip_path), audio=False)
        opened.append(raw)
        raw_seconds = float(raw.duration)
        audio, speech = None, 0.0
        if audio_path and Path(audio_path).is_file():
            audio = AudioFileClip(str(audio_path))
            opened.append(audio)
            speech = float(audio.duration)
        timing = plan_timing(raw_seconds, speech, tail_padding=tail_padding, min_page_seconds=min_page_seconds,
                             max_slowdown=max_slowdown)
        page_seconds, slowdown = timing["page_seconds"], timing["slowdown"]
        last_t = max(0.0, raw_seconds - 1.5 / (raw.fps or fps))

        need_fit = tuple(raw.size) != (width, height)
        if need_fit:
            scale = min(width / raw.w, height / raw.h)
            new = (max(2, round(raw.w * scale)), max(2, round(raw.h * scale)))
            offset = ((width - new[0]) // 2, (height - new[1]) // 2)
            canvas = np.empty((height, width, 3), dtype=np.uint8)
            canvas[:] = np.asarray(background, dtype=np.uint8)

        cues = schedule_subtitles(split_subtitles(narration, subtitle_max_chars), speech) if font_path else []
        overlays = []
        for cue in cues:
            rgba = render_subtitle(cue["text"], size, font_path).astype(np.float32)
            alpha = rgba[:, :, 3:4] / 255.0
            h, w = rgba.shape[:2]
            overlays.append((cue["start"], cue["end"], (width - w) // 2, height - h - round(height * 0.05),
                             rgba[:, :, :3] * alpha, 1.0 - alpha))

        def frame(t: float) -> np.ndarray:
            image = raw.get_frame(min(t / slowdown, last_t))
            if need_fit:
                resized = Image.fromarray(image).resize(new, Image.Resampling.BILINEAR)
                image = canvas.copy()
                image[offset[1]:offset[1] + new[1], offset[0]:offset[0] + new[0]] = np.asarray(resized)
            for start, end, x, y, premultiplied, inverse in overlays:
                if start <= t < end:
                    image = np.array(image)
                    h, w = premultiplied.shape[:2]
                    region = image[y:y + h, x:x + w].astype(np.float32)
                    image[y:y + h, x:x + w] = (region * inverse + premultiplied).astype(np.uint8)
                    break
            return image

        page = VideoClip(frame_function=frame, duration=page_seconds)
        if audio is not None:
            page = page.with_audio(audio)
        _write(page, out_path, fps, with_audio=audio is not None)
        return PageClipInfo(out_path, page_seconds, round(speech, 3), round(raw_seconds, 3), slowdown,
                            timing["freeze_seconds"], cues)
    finally:
        for clip in opened:
            try:
                clip.close()
            except Exception:
                pass


def plan_timeline(durations: list[float], crossfade: float) -> dict:
    """按各页时长和转场时长排出整本的时间轴。time_range 取 [本页开始, 下一页开始)。"""
    fade = max(0.0, min(crossfade, min(durations) / 2)) if len(durations) > 1 else 0.0
    starts, cursor = [], 0.0
    for index, duration in enumerate(durations):
        starts.append(round(cursor, 3))
        cursor += duration - (fade if index < len(durations) - 1 else 0.0)
    total = round(cursor, 3)
    ranges = [[starts[i], starts[i + 1] if i + 1 < len(starts) else total] for i in range(len(starts))]
    return {"duration": total, "starts": starts, "time_ranges": ranges, "crossfade": round(fade, 3)}


def concat_pages(page_clip_paths: list[Path], out_path: Path, *, size: tuple[int, int], fps: int,
                 crossfade: float) -> dict:
    """把各页成片按顺序拼成整本视频，页间交叉淡入。返回总时长和每页的 time_range。"""
    from bisect import bisect_right

    from moviepy import CompositeAudioClip, VideoClip, VideoFileClip
    if not page_clip_paths:
        raise ValueError("没有可拼接的片段")
    clips = [VideoFileClip(str(p)) for p in page_clip_paths]
    try:
        timeline = plan_timeline([float(c.duration) for c in clips], crossfade)
        starts, fade, total = timeline["starts"], timeline["crossfade"], timeline["duration"]
        safe_end = [max(0.0, float(c.duration) - 1.5 / (c.fps or fps)) for c in clips]

        def frame(t: float) -> np.ndarray:
            i = max(0, bisect_right(starts, t) - 1)
            local = t - starts[i]
            current = clips[i].get_frame(min(local, safe_end[i]))
            if i > 0 and fade > 0 and local < fade:        # 转场窗口：上一页淡出、本页淡入
                previous = clips[i - 1].get_frame(min(t - starts[i - 1], safe_end[i - 1]))
                a = local / fade
                current = (previous.astype(np.float32) * (1.0 - a) + current.astype(np.float32) * a).astype(np.uint8)
            return current

        final = VideoClip(frame_function=frame, duration=total)
        tracks = [c.audio.with_start(s) for c, s in zip(clips, starts) if c.audio is not None]
        if tracks:
            final = final.with_audio(CompositeAudioClip(tracks).with_duration(total))
        _write(final, out_path, fps, with_audio=bool(tracks))
        return {"duration": total, "time_ranges": timeline["time_ranges"], "crossfade": fade}
    finally:
        for clip in clips:
            try:
                clip.close()
            except Exception:
                pass
