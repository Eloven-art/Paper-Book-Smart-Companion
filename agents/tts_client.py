# -*- coding: utf-8 -*-
"""旁白配音。

真实服务：沿用参考代码（story_tts_server.py 的 infer_text）的 TTS 协议——
向 TTS_API_URL 发 POST，JSON 体为 {"text": "<speak ...>正文</speak>", "voice_params": {...}, "trace_id": ...}，
返回 JSON，其中 data 字段是 base64 编码的 WAV。

未配置 TTS_API_URL 时走占位方案：按字数估算时长，生成一段只有轻微提示音的 WAV。
占位音频只用来把视频时间轴和字幕跑通，结果里会标明 is_placeholder=true，不能当作配音成果。
"""
from __future__ import annotations

import base64
import math
import struct
import time
import wave
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Optional

import requests

from server.errors import ExternalServiceError

SENTENCE_END = "。！？!?；;…"
PAUSE_MARKS = "，、,：:"


@dataclass
class TTSResult:
    path: Path
    duration: float
    provider: str
    is_placeholder: bool
    elapsed_ms: int
    chars: int
    voice: str


def wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as wf:
        return wf.getnframes() / float(wf.getframerate() or 1)


def estimate_speech_seconds(text: str) -> float:
    """按儿童读物的朗读语速估算时长：约每秒 4 个字，句末停 0.35 秒，逗号停 0.15 秒。"""
    spoken = sum(1 for ch in text if ch not in SENTENCE_END + PAUSE_MARKS and not ch.isspace())
    pauses = sum(0.35 for ch in text if ch in SENTENCE_END) + sum(0.15 for ch in text if ch in PAUSE_MARKS)
    return round(max(1.5, spoken / 4.0 + pauses), 2)


def write_placeholder_wav(text: str, path: Path, rate: int = 24000) -> float:
    """占位音频：整体静音，在每句开头放一个很轻的短提示音，方便联调时确认音画同步。"""
    duration = estimate_speech_seconds(text)
    total = int(duration * rate)
    samples = [0] * total
    sentences, buf = [], ""
    for ch in text:
        buf += ch
        if ch in SENTENCE_END:
            sentences.append(buf)
            buf = ""
    if buf.strip():
        sentences.append(buf)
    cursor = 0.0
    full = sum(estimate_speech_seconds(s) for s in sentences) or 1.0
    for sentence in sentences:
        start = int(cursor / full * total)
        length = int(0.12 * rate)
        for i in range(min(length, total - start)):
            envelope = math.sin(math.pi * i / length)
            samples[start + i] = int(2200 * envelope * math.sin(2 * math.pi * 660 * i / rate))
        cursor += estimate_speech_seconds(sentence)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(struct.pack(f"<{total}h", *samples))
    return duration


class TTSClient:
    def __init__(self, url: Optional[str], tts_model: str = "v2_llm", timeout: float = 60.0,
                 session: Optional[requests.Session] = None):
        self.url = url
        self.tts_model = tts_model
        self.timeout = timeout
        self._session = session or requests.Session()

    @property
    def configured(self) -> bool:
        return bool(self.url)

    @property
    def provider(self) -> str:
        return "reference_tts_http" if self.configured else "placeholder_tone"

    def synthesize(self, text: str, out_path: Path, voice: str = "xiaosi", lang: str = "cn",
                   trace_id: str = "pbsc") -> TTSResult:
        out_path = Path(out_path)
        started = time.perf_counter()
        if not self.configured:
            duration = write_placeholder_wav(text, out_path)
            return TTSResult(out_path, duration, self.provider, True,
                             int((time.perf_counter() - started) * 1000), len(text), voice)

        voice_params = {"voice_type": voice, "emotion": "neutral", "lang": lang, "audio_format": "wav",
                        "rate": "1.0", "pitch": "1.0", "volume": "90", "tts_model": self.tts_model}
        speak = (f'<speak voice_type="{voice}" emotion="neutral" volume="90" rate="1.0" pitch="1.0" '
                 f'lang="{lang}" audio_format="wav" tts_model="{self.tts_model}">{escape(text.strip())}</speak>')
        try:
            resp = self._session.post(self.url, json={"text": speak, "voice_params": voice_params,
                                                      "trace_id": trace_id}, timeout=self.timeout)
        except requests.Timeout as exc:
            raise ExternalServiceError(f"TTS 请求超时（{self.timeout:.0f} 秒）", service="tts", retryable=True) from exc
        except requests.RequestException as exc:
            raise ExternalServiceError(f"TTS 请求失败：{type(exc).__name__}", service="tts",
                                       retryable=isinstance(exc, requests.ConnectionError)) from exc
        if resp.status_code >= 400:
            raise ExternalServiceError(f"TTS 返回 HTTP {resp.status_code}", service="tts", status=resp.status_code,
                                       retryable=resp.status_code in (408, 429) or resp.status_code >= 500,
                                       details={"body": resp.text[:300]})
        try:
            encoded = resp.json().get("data")
            audio = base64.b64decode(encoded) if encoded else b""
        except (ValueError, AttributeError, TypeError) as exc:
            raise ExternalServiceError("TTS 返回格式不符（期望 JSON 且含 base64 的 data 字段）", service="tts",
                                       details={"body": resp.text[:300]}) from exc
        if audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
            raise ExternalServiceError("TTS 没有返回 WAV 音频", service="tts", retryable=not audio)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = out_path.with_suffix(".part")
        tmp.write_bytes(audio)
        tmp.replace(out_path)
        return TTSResult(out_path, round(wav_duration(out_path), 3), self.provider, False,
                         int((time.perf_counter() - started) * 1000), len(text), voice)
