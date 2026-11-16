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
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from string import Template
from typing import Callable, Optional

from configs.settings import Settings
from server.book_store import BookStore
from server.errors import AppError, ValidationError
from server.retry import call_with_retry
from server.run_logger import RunLogger, now_iso, sha256_file
from server.voice_store import selected_voice_id

from .cost import CostLedger
from .fonts import find_cjk_font
from .i2v_client import dashscope_seconds, DashScopeI2V, GatewayI2V, KenBurnsI2V
from .story_agent import load_prompt, passthrough_narration
from .tts_client import build_tts_client, wav_duration
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
                 tts: Optional[TTSClient] = None, i2v=None):
        self.settings = settings
        self.cfg = settings.video
        self.store = store
        self.runs = run_logger
        self.ledger = ledger
        self.tts = tts or build_tts_client(settings)
        if i2v is not None:
            self.i2v = i2v
        elif settings.i2v_provider == "dashscope":
            self.i2v = DashScopeI2V(
                settings.dashscope_api_key, settings.dashscope_model,
                workspace=settings.dashscope_workspace, region=settings.dashscope_region,
                base_url=settings.dashscope_base_url,
                media_protocol=settings.dashscope_media_protocol, resolution=settings.dashscope_resolution,
                ratio=settings.dashscope_ratio, prompt_extend=settings.dashscope_prompt_extend,
                watermark=settings.dashscope_watermark, timeout=settings.dashscope_timeout,
                poll_interval=settings.dashscope_poll_interval,
                max_interval=settings.dashscope_max_interval)
        else:
            self.i2v = GatewayI2V(
                settings.i2v.url, settings.i2v.api_key, settings.i2v.model, priority=self.cfg.i2v_priority,
                resolution=self.cfg.resolution, aspect_ratio=self.cfg.aspect_ratio,
                image_mode=self.cfg.i2v_image_mode, max_image_side=self.cfg.i2v_max_image_side,
                poll_interval=self.cfg.i2v_poll_interval, max_wait=self.cfg.i2v_max_wait,
                public_url_for=self._public_url)
        self.fallback = KenBurnsI2V((self.cfg.width, self.cfg.height), self.cfg.fps, self.cfg.background)
        self.prompt = load_prompt(settings.prompts_dir, PROMPT_NAME)
        self.font = find_cjk_font(self.cfg.subtitle_font) if self.cfg.subtitles_enabled else None
        # CosyVoice 对并发请求限流，配音串行排队；视频片段仍然并发生成
        self._tts_lock = threading.Lock()
        # 云端图生视频额度用完/无权限时，短时间内直接走本地片段，避免每页都白试一次。
        self._cloud_blocked_until = 0.0

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
        voice = (selected_voice_id(self.settings.voice_selection_file)
                 or getattr(self.tts, "voice", None) or self.settings.tts_voice)
        key = _key({"text": narration, "voice": voice, "provider": self.tts.provider, "model": self.tts.tts_model})
        cached = _read_json(sidecar)
        if not force and path.is_file() and cached.get("cache_key") == key:
            return {**cached["audio"], "cached": True}
        with self.runs.start("tts", {"book_id": book["book_id"], "page_no": page["page_no"], "text": narration,
                                     "voice": voice}, parent_run_id) as run:
            run.set_model(self.tts.tts_model if self.tts.configured else None, {"voice": voice},
                          provider=self.tts.provider)
            def synthesize():
                with self.ledger.reserve("tts", self.tts.tts_model, {"chars": len(narration)},
                                         billable=self.tts.configured):
                    result = self.tts.synthesize(narration, path, voice, trace_id=run.run_id)
                    cost = self.ledger.record("tts", self.tts.tts_model, {"chars": result.chars},
                                              run_id=run.run_id, billable=not result.is_placeholder,
                                              book_id=book["book_id"], page_no=page["page_no"])
                    return result, cost
            with self._tts_lock:
                result, cost = call_with_retry(synthesize, attempts=max(4, self.settings.retry_attempts),
                    base_delay=max(3.0, self.settings.retry_base_delay), label="旁白配音")
            run.set_cost(cost)
            run.add_output(path, "narration_audio", duration=result.duration)
            run.note("degraded", result.is_placeholder)
            audio = {"path": rel, "duration": result.duration, "provider": result.provider,
                     "is_placeholder": result.is_placeholder, "voice": voice, "run_id": run.run_id}
        _write_json(sidecar, {"cache_key": key, "audio": audio})
        return {**audio, "cached": False}

    def generate_page_audio(self, book_id: str, page_no: int, force: bool = False,
                            parent_run_id: Optional[str] = None,
                            notify: Optional[Callable[[str], None]] = None) -> dict:
        """只生成一页配音并写回 narration_audio，不做视频。"""
        notify = notify or (lambda message: None)
        book = self.store.load(book_id)
        page = BookStore.get_page(book, page_no)
        narration = (page.get("narration") or "").strip()
        source = "narration"
        if not narration:
            narration = passthrough_narration(page)
            source = "ocr_passthrough"
        if not narration:
            raise ValidationError(f"第 {page_no} 页没有可朗读的文字", code="PAGE_TEXT_MISSING")
        notify(f"第 {page_no} 页：配音")
        audio = self.ensure_audio(book, page, narration, parent_run_id, force)

        def mutate(current: dict) -> None:
            target = BookStore.get_page(current, page_no)
            target["narration_audio"] = {k: v for k, v in audio.items() if k != "cached"}

        self.store.update(book_id, mutate)
        return {"page_no": page_no, "narration": narration, "narration_source": source,
                "run_id": audio.get("run_id"), **{k: audio.get(k) for k in
                ("path", "duration", "provider", "is_placeholder", "voice", "cached")}}

    # ----- 图生视频片段 -----
    def _cloud_page_allowed(self, page_no: int) -> bool:
        # 0：全部使用云端。正数：只用在前 N 页。负数：仅本地。
        max_ai = self.settings.i2v_max_pages
        return max_ai == 0 or (0 < page_no <= max_ai)

    def _cloud_available(self, page_no: int) -> bool:
        return (self.i2v.configured and self._cloud_page_allowed(page_no)
                and time.monotonic() >= self._cloud_blocked_until)

    def ensure_raw_clip(self, book: dict, page: dict, prompt: str, parent_run_id: str, force: bool,
                        notify: Callable[[str], None]) -> dict:
        book_dir = self.store.book_dir(book["book_id"])
        image_path = self.store.resolve_media(book["book_id"], page["page_image"])
        rel = f"video/raw/page_{page['page_no']:03d}.mp4"
        path, sidecar = book_dir / rel, book_dir / f"video/raw/page_{page['page_no']:03d}.json"
        seconds = float(self.cfg.clip_seconds)
        image_sha = sha256_file(image_path)
        page_allowed = self._cloud_page_allowed(page["page_no"])
        primary = self.i2v if self._cloud_available(page["page_no"]) else self.fallback

        def cache_key(provider) -> str:
            if provider is self.i2v and self.settings.i2v_provider == "dashscope":
                resolution, ratio = self.settings.dashscope_resolution, self.settings.dashscope_ratio
                media_protocol = self.settings.dashscope_media_protocol
            else:
                resolution, ratio = self.cfg.resolution, self.cfg.aspect_ratio
                media_protocol = self.cfg.i2v_image_mode
            return _key({"image": image_sha, "prompt": prompt, "provider": provider.provider,
                         "model": provider.model, "seconds": seconds,
                         "resolution": resolution, "aspect_ratio": ratio,
                         "media_protocol": media_protocol,
                         "size": [self.cfg.width, self.cfg.height]})

        cached = _read_json(sidecar)
        if not force and path.is_file() and cached.get("clip"):
            key = cached.get("cache_key")
            # 配额暂不可用时，仍优先使用之前成功生成的同款云端片段。
            if ((page_allowed and self.i2v.configured and key == cache_key(self.i2v))
                    or (primary is self.fallback and key == cache_key(self.fallback))):
                return {**cached["clip"], "cached": True}

        run_input = {"book_id": book["book_id"], "page_no": page["page_no"], "first_frame": page["page_image"],
                     "first_frame_sha256": image_sha, "prompt": prompt, "seconds": seconds}
        with self.runs.start("i2v", run_input, parent_run_id) as run:
            run.set_prompt(self.prompt.version, self.prompt.sha256, rendered=prompt)
            fallback_reason = None
            result = None
            if primary is self.i2v:
                if self.settings.i2v_provider == "dashscope":
                    model_params = {"seconds": seconds, "resolution": self.settings.dashscope_resolution,
                                    "ratio": self.settings.dashscope_ratio,
                                    "media_protocol": self.settings.dashscope_media_protocol}
                else:
                    model_params = {"seconds": seconds, "resolution": self.cfg.resolution,
                                    "aspect_ratio": self.cfg.aspect_ratio,
                                    "image_mode": self.cfg.i2v_image_mode}
                run.set_model(self.i2v.model, model_params, provider=self.i2v.provider)
                try:
                    def generate_cloud():
                        billed_seconds = dashscope_seconds(seconds) if self.settings.i2v_provider == "dashscope" else max(seconds, round(seconds))
                        units = {"seconds": billed_seconds, "calls": 1}
                        with self.ledger.reserve("i2v", self.i2v.model, units):
                            clip = self.i2v.generate(image_path, prompt, path, seconds, on_progress=notify)
                            charge = self.ledger.record("i2v", self.i2v.model, units,
                                run_id=run.run_id, billable=clip.billable, book_id=book["book_id"],
                                page_no=page["page_no"], note=clip.provider)
                            return clip, charge
                    result, cost = call_with_retry(
                        generate_cloud,
                        attempts=self.settings.retry_attempts, base_delay=self.settings.retry_base_delay,
                        label="图生视频")
                except AppError as exc:
                    fallback_reason = f"图生视频调用失败（{exc.code}）：{exc.message}"
                    run.note("gateway_error", exc.to_dict())
                    text = f"{exc.code} {exc.message}"
                    if getattr(exc, "status", None) in (401, 403) or any(
                            token in text for token in ("Free quota", "Unpurchased", "AccessDenied")):
                        # 额度用尽/无模型权限：接下来一段时间直接走本地，避免整本逐页白试。
                        self._cloud_blocked_until = time.monotonic() + float(
                            os.getenv("PBSC_I2V_BLOCK_SECONDS", "900") or 900)
            else:
                if not self.i2v.configured:
                    missing = ("DASHSCOPE_API_KEY" if self.settings.i2v_provider == "dashscope"
                               else "IMAGE2VIDEO_URL")
                    fallback_reason = f"图生视频服务未配置（{missing}），使用本地降级片段"
                elif not page_allowed:
                    fallback_reason = "按设置只有部分页使用云端 AI，本页使用本地推近片段"
                else:
                    fallback_reason = "云端图生视频额度/权限暂不可用，本页使用本地推近片段"
                run.set_model(None, {"seconds": seconds}, provider=self.fallback.provider)
            if result is None:
                notify("使用本地降级片段（静态页图缓慢推近）")
                result = self.fallback.generate(image_path, prompt, path, seconds)
                cost = self.ledger.record("i2v", result.model, {"seconds": seconds, "calls": 1},
                    run_id=run.run_id, billable=False, book_id=book["book_id"], page_no=page["page_no"],
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
            if narration and self.tts.configured and self._cloud_available(page_no):
                notify(f"第 {page_no} 页：配音与图生视频同时进行")
                with ThreadPoolExecutor(max_workers=1, thread_name_prefix="pbsc-video-audio") as pool:
                    audio_job = pool.submit(self.ensure_audio, book, page, narration, run.run_id, force)
                    raw = self.ensure_raw_clip(book, page, prompt, run.run_id, force, notify)
                    audio = audio_job.result()
            else:
                notify(f"第 {page_no} 页：配音")
                audio = self.ensure_audio(book, page, narration, run.run_id, force) if narration else None
                notify(f"第 {page_no} 页：生成视频片段")
                raw = self.ensure_raw_clip(book, page, prompt, run.run_id, force, notify)

            compose_key = _key({"raw": raw["run_id"], "audio": audio["run_id"] if audio else None,
                                "narration": narration, "size": [self.cfg.width, self.cfg.height],
                                "fps": self.cfg.fps, "tail": self.cfg.tail_padding,
                                "min": self.cfg.min_page_seconds, "slow": self.cfg.max_slowdown,
                                "subtitles_enabled": self.cfg.subtitles_enabled,
                                "font": (self.font or {}).get("path"), "chars": self.cfg.subtitle_max_chars})
            sidecar = book_dir / f"video/clips/{clip_id}.json"
            cached = _read_json(sidecar)
            info = cached.get("info") or {}
            compose_cached = (not force and cached.get("cache_key") == compose_key
                              and (book_dir / clip_rel).is_file()
                              and sha256_file(book_dir / clip_rel) == info.get("clip_sha256"))
            if compose_cached:
                run.note("compose_cached", True)
            else:
                notify(f"第 {page_no} 页：合成画面与配音")
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
            if not self.cfg.subtitles_enabled:
                run.note("subtitle_skipped", "默认关闭画面字幕，避免遮挡原书文字")
            elif not self.font:
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
            previous = p.get("video_meta") or {}
            changed = previous.get("clip_sha256") != video_meta["clip_sha256"]
            p["video_clip_id"] = clip_id
            p["video_prompt"] = prompt
            # 缓存命中且内容相同时，保留原成片的来源信息和审核指纹。
            if not compose_cached or previous != dict(
                    video_meta, run_id=previous.get("run_id"), generated_at=previous.get("generated_at")):
                p["video_meta"] = video_meta
            p["narration_audio"] = audio_meta
            if changed and b.get("video"):
                b["video"]["stale"] = True          # 有页面的片段变了，整本视频需要重新拼接

        self.store.update(book_id, mutate)
        return {"page_no": page_no, "video_clip_id": clip_id, "clip_path": clip_rel, "duration": info["duration"],
                "clip_sha256": info["clip_sha256"],
                "provider": raw["provider"], "is_fallback": raw["is_fallback"], "raw_cached": raw["cached"],
                "audio_placeholder": bool((audio or {}).get("is_placeholder")), "run_id": run.run_id,
                "narration": narration, "prompt": prompt}

    # ----- 整本 -----
    def generate_book_video(self, book_id: str, force: bool = False, progress: Progress = None,
                            check_cancelled: Optional[Callable[[], None]] = None, pages: Optional[list[int]] = None) -> dict:
        """逐页生成成片后拼成整本视频，把每页的 time_range 写回分页数据，并导出片段索引。"""
        book = self.store.load(book_id)
        page_nos = sorted(p["page_no"] for p in book.get("pages") or []
                          if not p.get("import_skipped") and (pages is None or p["page_no"] in pages))
        if not page_nos:
            raise ValidationError("这本书还没有页面", code="BOOK_EMPTY")
        report = progress or (lambda fraction, message: None)
        book_dir = self.store.book_dir(book_id)

        with self.runs.start("video_book", {"book_id": book_id, "pages": page_nos, "force": force}) as run:
            clips = []
            workers = max(1, int(getattr(self.settings, "video_workers", 1)))
            if workers > 1 and len(page_nos) > 1:
                total = len(page_nos)
                finished = 0
                failures = []
                with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="pbsc-video") as pool:
                    def generate(page_no):
                        if check_cancelled:
                            check_cancelled()
                        return self.generate_page_clip(book_id, page_no, force, run.run_id, None)
                    futures = {pool.submit(generate, page_no): page_no for page_no in page_nos}
                    for future in as_completed(futures):
                        page_no = futures[future]
                        try:
                            clips.append(future.result())
                        except Exception as exc:       # noqa: BLE001 - 等全部收尾后再抛
                            failures.append((page_no, exc))
                        if check_cancelled:
                            check_cancelled()
                        finished += 1
                        report(0.85 * finished / total,
                               f"已完成 {finished}/{total} 页（{workers} 页并发生成）")
                if failures:
                    retry_failed = []
                    for page_no, exc in failures:
                        if check_cancelled:
                            check_cancelled()
                        report(0.85, f"重试第 {page_no} 页")
                        try:
                            clips.append(self.generate_page_clip(book_id, page_no, force, run.run_id, None))
                        except Exception as retry_exc:    # noqa: BLE001 - 记录后统一抛错
                            retry_failed.append((page_no, retry_exc))
                    if retry_failed:
                        page_no, exc = retry_failed[0]
                        if isinstance(exc, AppError):
                            raise exc
                        raise AppError(f"第 {page_no} 页视频生成失败：{exc}", code="VIDEO_FAILED",
                                       http_status=502, details={"page_no": page_no}) from exc
                clips.sort(key=lambda clip: clip["page_no"])
            else:
                for index, page_no in enumerate(page_nos):
                    if check_cancelled:
                        check_cancelled()
                    base = index / len(page_nos) * 0.85
                    report(base, f"第 {page_no} 页（{index + 1}/{len(page_nos)}）")
                    clips.append(self.generate_page_clip(book_id, page_no, force, run.run_id,
                                                         notify=lambda m, b=base: report(b, m)))
            if check_cancelled:
                check_cancelled()
            video_rel = "video/book.mp4"
            video_path = book_dir / video_rel
            index_path = book_dir / "video/clip_index.json"
            merge_key = _key({"clips": [(c["page_no"], c["clip_sha256"], c["prompt"], c["narration"],
                                        c["provider"], c["is_fallback"]) for c in clips],
                              "size": [self.cfg.width, self.cfg.height], "fps": self.cfg.fps,
                              "crossfade": self.cfg.crossfade})
            previous = self.store.load(book_id).get("video") or {}
            merge_cached = (not force and not previous.get("stale") and previous.get("merge_key") == merge_key
                            and video_path.is_file() and sha256_file(video_path) == previous.get("sha256"))
            run.note("merge_cached", merge_cached)
            if merge_cached:
                report(0.88, "复用已生成的整本视频")
                video = previous
                if not index_path.is_file():
                    _write_json(index_path, {"book_id": book_id, **video})
            else:
                report(0.88, "拼接整本视频")
                video = self._merge_book(book_id, book_dir, video_rel, index_path, clips, merge_key, run.run_id)
            run.add_output(video_path, "book_video", duration=video["duration"])
            run.add_output(index_path, "clip_index")
            run.note("degraded", any(c["is_fallback"] or c["audio_placeholder"] for c in clips))
            run.note("pages", [{k: c[k] for k in ("page_no", "provider", "is_fallback", "raw_cached", "run_id")}
                               for c in clips])
        report(1.0, "视频生成完成")
        return {"run_id": run.run_id, "book_id": book_id, "video_path": video_rel, "duration": video["duration"],
                "clips": video["clips"], "fallback_pages": [c["page_no"] for c in clips if c["is_fallback"]],
                "placeholder_audio_pages": [c["page_no"] for c in clips if c["audio_placeholder"]]}

    def _merge_book(self, book_id: str, book_dir: Path, video_rel: str, index_path: Path,
                    clips: list[dict], merge_key: str, run_id: str) -> dict:
        merged = concat_pages([book_dir / c["clip_path"] for c in clips], book_dir / video_rel,
                              size=(self.cfg.width, self.cfg.height), fps=self.cfg.fps,
                              crossfade=self.cfg.crossfade)
        index_entries = []
        for clip, time_range in zip(clips, merged["time_ranges"]):
            index_entries.append({"video_clip_id": clip["video_clip_id"], "page_no": clip["page_no"],
                                  "time_range": time_range, "narration": clip["narration"],
                                  "prompt": clip["prompt"], "clip_path": clip["clip_path"],
                                  "provider": clip["provider"], "is_fallback": clip["is_fallback"],
                                  "run_id": clip["run_id"]})
        video = {"path": video_rel, "duration": merged["duration"], "fps": self.cfg.fps,
                 "size": {"width": self.cfg.width, "height": self.cfg.height},
                 "crossfade": merged["crossfade"], "run_id": run_id, "generated_at": now_iso(),
                 "stale": False, "sha256": sha256_file(book_dir / video_rel), "clips": index_entries,
                 "merge_key": merge_key}

        def mutate(b: dict) -> None:
            same_video = (b.get("video") or {}).get("sha256") == video["sha256"]
            for entry in index_entries:
                page = BookStore.get_page(b, entry["page_no"])
                page["time_range"] = entry["time_range"]
                if not same_video:
                    page["keyframes"] = []
                    page.pop("video_index", None)
            b["video"] = video

        self.store.update(book_id, mutate)
        _write_json(index_path, {"book_id": book_id, **video})
        return video
