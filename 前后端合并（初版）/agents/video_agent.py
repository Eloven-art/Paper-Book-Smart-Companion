# -*- coding: utf-8 -*-
"""视频生成节点（A 步骤 5）：逐页“页图 → 图生视频片段 + 旁白配音 + 字幕”，再拼成整本视频。

要点：
- 首帧就是原绘本页插图，保证画风与原书一致；
- 提示词由本页画面摘要、情节、人物外观设定（B 提供）和前后页摘要拼成，保证跨页连贯
  （模板见 configs/prompts/video_v1.txt）；
- MoviePy 完成片段补时、配音、字幕和页间转场；
- 每个片段的页码、time_range、旁白、提示词写回分页数据，并导出片段索引 video/clip_index.json 供 B 建检索；
- 每次外部调用前检查预算、调用后记入费用台账；片段和配音按输入内容做缓存，输入没变就不重复花钱。

图生视频服务未配置或调用失败时，用本地降级片段（静态页图缓慢推近）顶上，并在数据里标明 is_fallback。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from string import Template
from typing import Callable, Optional

from configs.settings import Settings
from server.book_store import BookStore
from server.errors import AppError, ValidationError
from server.retry import call_with_retry
from server.run_logger import RunLogger, now_iso, sha256_file

from .cost import CostLedger
from .fonts import find_cjk_font
from .i2v_client import GatewayI2V, KenBurnsI2V
from .story_agent import load_prompt, passthrough_narration
from .tts_client import TTSClient, wav_duration
from .video_compose import compose_page, concat_pages, probe_duration

PROMPT_NAME = "video_v1"
Progress = Optional[Callable[[float, str], None]]


def _key(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class VideoAgent:
    def __init__(self, settings: Settings, store: BookStore, run_logger: RunLogger, ledger: CostLedger,
                 tts: Optional[TTSClient] = None, i2v: Optional[GatewayI2V] = None):
        self.settings = settings
        self.cfg = settings.video
        self.store = store
        self.runs = run_logger
        self.ledger = ledger
        self.tts = tts or TTSClient(settings.tts.url, settings.tts.model or "v2_llm")
        self.i2v = i2v or GatewayI2V(
            settings.i2v.url, settings.i2v.api_key, settings.i2v.model, priority=self.cfg.i2v_priority,
            resolution=self.cfg.resolution, aspect_ratio=self.cfg.aspect_ratio, image_mode=self.cfg.i2v_image_mode,
            max_image_side=self.cfg.i2v_max_image_side, poll_interval=self.cfg.i2v_poll_interval,
            max_wait=self.cfg.i2v_max_wait, public_url_for=self._public_url)
        self.fallback = KenBurnsI2V((self.cfg.width, self.cfg.height), self.cfg.fps, self.cfg.background)
        self.prompt = load_prompt(settings.prompts_dir, PROMPT_NAME)
        self.font = find_cjk_font(self.cfg.subtitle_font)

    # ----- 小工具 -----
    def _public_url(self, path: Path) -> Optional[str]:
        if not self.settings.public_base_url:
            return None
        rel = Path(path).resolve().relative_to(self.store.books_dir.resolve()).as_posix()
        return f"{self.settings.public_base_url.rstrip('/')}/api/v1/media/{rel}"

    @staticmethod
    def clip_id(book_id: str, page_no: int) -> str:
        return f"{book_id}_p{page_no:03d}"

    def build_prompt(self, book: dict, page: dict) -> str:
        """拼图生视频提示词：本页画面 + 动作 + 人物外观 + 前后页衔接。"""
        pages = sorted(book.get("pages") or [], key=lambda p: p["page_no"])
        index = next(i for i, p in enumerate(pages) if p["page_no"] == page["page_no"])
        by_name = {c.get("name"): c for c in book.get("characters") or []}
        people = []
        for c in page.get("characters") or []:
            appearance = c.get("appearance") or (by_name.get(c.get("name")) or {}).get("appearance", "")
            people.append(f"{c.get('name')}（{appearance}）" if appearance else str(c.get("name")))
        prev_summary = pages[index - 1].get("scene_summary") if index > 0 else ""
        next_summary = pages[index + 1].get("scene_summary") if index + 1 < len(pages) else ""
        continuity = "；".join(filter(None, [
            f"上一页是“{prev_summary}”" if prev_summary else "这是全书第一页",
            f"下一页是“{next_summary}”" if next_summary else "这是全书最后一页"]))
        values = {
            "scene_summary": page.get("scene_summary") or "保持原图内容",
            "motion": "；".join(page.get("plot_events") or []) or "画面安静，只有轻微的自然动态",
            "characters": "；".join(people) or "无明确人物",
            "continuity": continuity,
            "age_range": book.get("age_range") or "3-8",
        }
        return Template(self.prompt.sections["template"]).safe_substitute(values).strip()

    # ----- 配音 -----
    def ensure_audio(self, book: dict, page: dict, narration: str, parent_run_id: str, force: bool) -> dict:
        book_dir = self.store.book_dir(book["book_id"])
        rel = f"audio/page_{page['page_no']:03d}.wav"
        path, sidecar = book_dir / rel, book_dir / f"audio/page_{page['page_no']:03d}.json"
        voice = self.settings.tts_voice
        key = _key({"text": narration, "voice": voice, "provider": self.tts.provider, "model": self.tts.tts_model})
        cached = _read_json(sidecar)
        if not force and path.is_file() and cached.get("cache_key") == key:
            return {**cached["audio"], "cached": True}
        with self.runs.start("tts", {"book_id": book["book_id"], "page_no": page["page_no"], "text": narration,
                                     "voice": voice}, parent_run_id) as run:
            run.set_model(self.tts.tts_model if self.tts.configured else None, {"voice": voice},
                          provider=self.tts.provider)
            if self.tts.configured:
                self.ledger.check("tts")
            result = call_with_retry(lambda: self.tts.synthesize(narration, path, voice, trace_id=run.run_id),
                                     attempts=self.settings.retry_attempts,
                                     base_delay=self.settings.retry_base_delay, label="旁白配音")
            cost = self.ledger.record("tts", self.tts.tts_model if self.tts.configured else None,
                                      {"chars": result.chars}, run_id=run.run_id,
                                      billable=not result.is_placeholder, book_id=book["book_id"],
                                      page_no=page["page_no"])
            run.set_cost(cost)
            run.add_output(path, "narration_audio", duration=result.duration)
            run.note("degraded", result.is_placeholder)
            audio = {"path": rel, "duration": result.duration, "provider": result.provider,
                     "is_placeholder": result.is_placeholder, "voice": voice, "run_id": run.run_id}
        _write_json(sidecar, {"cache_key": key, "audio": audio})
        return {**audio, "cached": False}

    # ----- 图生视频片段 -----
    def ensure_raw_clip(self, book: dict, page: dict, prompt: str, parent_run_id: str, force: bool,
                        notify: Callable[[str], None]) -> dict:
        book_dir = self.store.book_dir(book["book_id"])
        image_path = self.store.resolve_media(book["book_id"], page["page_image"])
        rel = f"video/raw/page_{page['page_no']:03d}.mp4"
        path, sidecar = book_dir / rel, book_dir / f"video/raw/page_{page['page_no']:03d}.json"
        seconds = float(self.cfg.clip_seconds)
        image_sha = sha256_file(image_path)
        primary = self.i2v if self.i2v.configured else self.fallback

        def cache_key(provider) -> str:
            return _key({"image": image_sha, "prompt": prompt, "provider": provider.provider,
                         "model": provider.model, "seconds": seconds,
                         "resolution": self.cfg.resolution, "aspect_ratio": self.cfg.aspect_ratio,
                         "size": [self.cfg.width, self.cfg.height]})

        cached = _read_json(sidecar)
        if not force and path.is_file() and cached.get("cache_key") == cache_key(primary):
            return {**cached["clip"], "cached": True}

        run_input = {"book_id": book["book_id"], "page_no": page["page_no"], "first_frame": page["page_image"],
                     "first_frame_sha256": image_sha, "prompt": prompt, "seconds": seconds}
        with self.runs.start("i2v", run_input, parent_run_id) as run:
            run.set_prompt(self.prompt.version, self.prompt.sha256, rendered=prompt)
            fallback_reason = None
            result = None
            if primary is self.i2v:
                run.set_model(self.i2v.model, {"seconds": seconds, "resolution": self.cfg.resolution,
                                               "aspect_ratio": self.cfg.aspect_ratio,
                                               "image_mode": self.cfg.i2v_image_mode}, provider=self.i2v.provider)
                try:
                    self.ledger.check("i2v")
                    result = call_with_retry(
                        lambda: self.i2v.generate(image_path, prompt, path, seconds, on_progress=notify),
                        attempts=self.settings.retry_attempts, base_delay=self.settings.retry_base_delay,
                        label="图生视频")
                except AppError as exc:
                    fallback_reason = f"图生视频调用失败（{exc.code}）：{exc.message}"
                    run.note("gateway_error", exc.to_dict())
            else:
                fallback_reason = "图生视频服务未配置（IMAGE2VIDEO_URL），使用本地降级片段"
                run.set_model(None, {"seconds": seconds}, provider=self.fallback.provider)
            if result is None:
                notify("使用本地降级片段（静态页图缓慢推近）")
                result = self.fallback.generate(image_path, prompt, path, seconds)
            cost = self.ledger.record("i2v", result.model, {"seconds": seconds, "calls": 1}, run_id=run.run_id,
                                      billable=result.billable, book_id=book["book_id"], page_no=page["page_no"],
                                      note=result.provider)
            run.set_cost(cost)
            run.add_output(path, "raw_clip", seconds=result.seconds)
            run.note("degraded", result.is_fallback)
            run.note("provider_details", result.details)
            clip = {"path": rel, "seconds": result.seconds, "provider": result.provider, "model": result.model,
                    "is_fallback": result.is_fallback, "fallback_reason": fallback_reason,
                    "task_id": result.task_id, "run_id": run.run_id, "cost": cost,
                    "elapsed_ms": result.elapsed_ms}
        used = self.fallback if result.is_fallback else self.i2v
        _write_json(sidecar, {"cache_key": cache_key(used), "clip": clip})
        return {**clip, "cached": False}

    # ----- 单页成片 -----
    def generate_page_clip(self, book_id: str, page_no: int, force: bool = False,
                           parent_run_id: Optional[str] = None,
                           notify: Optional[Callable[[str], None]] = None) -> dict:
        """生成一页的成片（片段 + 配音 + 字幕），并把片段信息写回分页数据。time_range 由整本拼接时写入。"""
        notify = notify or (lambda message: None)
        book = self.store.load(book_id)
        page = BookStore.get_page(book, page_no)
        if not page.get("page_image"):
            raise ValidationError(f"第 {page_no} 页没有页面图片，无法生成视频", code="PAGE_IMAGE_MISSING")
        narration = (page.get("narration") or "").strip()
        narration_source = "narration"
        if not narration:
            narration, narration_source = passthrough_narration(page), "ocr_passthrough"
        clip_id = self.clip_id(book_id, page_no)
        book_dir = self.store.book_dir(book_id)
        clip_rel = f"video/clips/{clip_id}.mp4"

        with self.runs.start("video_page", {"book_id": book_id, "page_no": page_no, "force": force,
                                            "narration": narration}, parent_run_id) as run:
            if not (book_dir / page["page_image"]).is_file():
                raise ValidationError(f"第 {page_no} 页的页面图片不存在：{page['page_image']}",
                                      code="PAGE_IMAGE_MISSING", http_status=404)
            prompt = self.build_prompt(book, page)
            run.set_prompt(self.prompt.version, self.prompt.sha256, rendered=prompt)
            notify(f"第 {page_no} 页：配音")
            audio = self.ensure_audio(book, page, narration, run.run_id, force) if narration else None
            notify(f"第 {page_no} 页：生成视频片段")
            raw = self.ensure_raw_clip(book, page, prompt, run.run_id, force, notify)

            compose_key = _key({"raw": raw["run_id"], "audio": audio["run_id"] if audio else None,
                                "narration": narration, "size": [self.cfg.width, self.cfg.height],
                                "fps": self.cfg.fps, "tail": self.cfg.tail_padding,
                                "min": self.cfg.min_page_seconds, "slow": self.cfg.max_slowdown,
                                "font": (self.font or {}).get("path"), "chars": self.cfg.subtitle_max_chars})
            sidecar = book_dir / f"video/clips/{clip_id}.json"
            cached = _read_json(sidecar)
            if not force and (book_dir / clip_rel).is_file() and cached.get("cache_key") == compose_key:
                info = cached["info"]
                run.note("compose_cached", True)
            else:
                notify(f"第 {page_no} 页：合成配音与字幕")
                composed = compose_page(
                    book_dir / raw["path"], book_dir / audio["path"] if audio else None, narration,
                    book_dir / clip_rel, size=(self.cfg.width, self.cfg.height), fps=self.cfg.fps,
                    background=self.cfg.background, tail_padding=self.cfg.tail_padding,
                    min_page_seconds=self.cfg.min_page_seconds, max_slowdown=self.cfg.max_slowdown,
                    font_path=(self.font or {}).get("path"), subtitle_max_chars=self.cfg.subtitle_max_chars)
                info = {"duration": composed.duration, "speech_seconds": composed.speech_seconds,
                        "raw_clip_seconds": composed.raw_clip_seconds, "slowdown": composed.slowdown,
                        "freeze_seconds": composed.freeze_seconds, "subtitles": composed.subtitles,
                        "clip_sha256": sha256_file(book_dir / clip_rel)}
                _write_json(sidecar, {"cache_key": compose_key, "info": info})
            run.add_output(book_dir / clip_rel, "page_clip", duration=info["duration"])
            if not self.font:
                run.note("subtitle_skipped", "找不到中文字体，未生成字幕；可用 PBSC_SUBTITLE_FONT 指定字体文件")
            run.note("degraded", bool(raw["is_fallback"] or (audio or {}).get("is_placeholder")))

            video_meta = {
                "run_id": run.run_id, "clip_path": clip_rel, "clip_duration": info["duration"],
                "clip_sha256": info["clip_sha256"], "raw_clip_path": raw["path"],
                "raw_clip_seconds": info["raw_clip_seconds"], "provider": raw["provider"], "model": raw["model"],
                "is_fallback": raw["is_fallback"], "fallback_reason": raw["fallback_reason"],
                "i2v_run_id": raw["run_id"], "prompt_version": self.prompt.version,
                "speech_seconds": info["speech_seconds"], "slowdown": info["slowdown"],
                "freeze_seconds": info["freeze_seconds"], "subtitles": info["subtitles"],
                "subtitle_font": (self.font or {}).get("name"), "narration_source": narration_source,
                "generated_at": now_iso(),
            }
            audio_meta = {k: v for k, v in (audio or {}).items() if k != "cached"} or None

        def mutate(b: dict) -> None:
            p = BookStore.get_page(b, page_no)
            changed = (p.get("video_meta") or {}).get("clip_sha256") != video_meta["clip_sha256"]
            p["video_clip_id"] = clip_id
            p["video_prompt"] = prompt
            p["video_meta"] = video_meta
            p["narration_audio"] = audio_meta
            if changed and b.get("video"):
                b["video"]["stale"] = True          # 有页面的片段变了，整本视频需要重新拼接

        self.store.update(book_id, mutate)
        return {"page_no": page_no, "video_clip_id": clip_id, "clip_path": clip_rel, "duration": info["duration"],
                "provider": raw["provider"], "is_fallback": raw["is_fallback"], "raw_cached": raw["cached"],
                "audio_placeholder": bool((audio or {}).get("is_placeholder")), "run_id": video_meta["run_id"],
                "narration": narration, "prompt": prompt}

    # ----- 整本 -----
    def generate_book_video(self, book_id: str, force: bool = False, progress: Progress = None,
                            check_cancelled: Optional[Callable[[], None]] = None) -> dict:
        """逐页生成成片后拼成整本视频，把每页的 time_range 写回分页数据，并导出片段索引。"""
        book = self.store.load(book_id)
        page_nos = sorted(p["page_no"] for p in book.get("pages") or [])
        if not page_nos:
            raise ValidationError("这本书还没有页面", code="BOOK_EMPTY")
        report = progress or (lambda fraction, message: None)
        book_dir = self.store.book_dir(book_id)

        with self.runs.start("video_book", {"book_id": book_id, "pages": page_nos, "force": force}) as run:
            clips = []
            for index, page_no in enumerate(page_nos):
                if check_cancelled:
                    check_cancelled()
                base = index / len(page_nos) * 0.85
                report(base, f"第 {page_no} 页（{index + 1}/{len(page_nos)}）")
                clips.append(self.generate_page_clip(book_id, page_no, force, run.run_id,
                                                     notify=lambda m, b=base: report(b, m)))
            if check_cancelled:
                check_cancelled()
            report(0.88, "拼接整本视频")
            video_rel = "video/book.mp4"
            merged = concat_pages([book_dir / c["clip_path"] for c in clips], book_dir / video_rel,
                                  size=(self.cfg.width, self.cfg.height), fps=self.cfg.fps,
                                  crossfade=self.cfg.crossfade)
            run.add_output(book_dir / video_rel, "book_video", duration=merged["duration"])

            index_entries = []
            for clip, time_range in zip(clips, merged["time_ranges"]):
                index_entries.append({"video_clip_id": clip["video_clip_id"], "page_no": clip["page_no"],
                                      "time_range": time_range, "narration": clip["narration"],
                                      "prompt": clip["prompt"], "clip_path": clip["clip_path"],
                                      "provider": clip["provider"], "is_fallback": clip["is_fallback"],
                                      "run_id": clip["run_id"]})
            video = {"path": video_rel, "duration": merged["duration"], "fps": self.cfg.fps,
                     "size": {"width": self.cfg.width, "height": self.cfg.height},
                     "crossfade": merged["crossfade"], "run_id": run.run_id, "generated_at": now_iso(),
                     "stale": False, "sha256": sha256_file(book_dir / video_rel), "clips": index_entries}

            def mutate(b: dict) -> None:
                for entry in index_entries:
                    BookStore.get_page(b, entry["page_no"])["time_range"] = entry["time_range"]
                b["video"] = video

            self.store.update(book_id, mutate)
            _write_json(book_dir / "video/clip_index.json", {"book_id": book_id, **video})
            run.add_output(book_dir / "video/clip_index.json", "clip_index")
            run.note("degraded", any(c["is_fallback"] or c["audio_placeholder"] for c in clips))
            run.note("pages", [{k: c[k] for k in ("page_no", "provider", "is_fallback", "raw_cached", "run_id")}
                               for c in clips])
        report(1.0, "视频生成完成")
        return {"run_id": run.run_id, "book_id": book_id, "video_path": video_rel, "duration": merged["duration"],
                "clips": index_entries, "fallback_pages": [c["page_no"] for c in clips if c["is_fallback"]],
                "placeholder_audio_pages": [c["page_no"] for c in clips if c["audio_placeholder"]]}
