# -*- coding: utf-8 -*-
"""阿里云百炼 CosyVoice WebSocket 语音合成客户端。

协议（与官方文档一致）：
1. 建立 WebSocket，发 run-task（模型、音色、采样率、格式等）；
2. 收到 task-started 后发 continue-task（待合成文本）；
3. 发 finish-task，等待 task-finished；
4. 期间收到的二进制帧就是音频，拼接后保存。

密钥只从环境变量读取（COSYVOICE_API_KEY 或 DASHSCOPE_API_KEY），不写进代码/日志/前端。
未配置时和原 TTSClient 一样生成占位音频，保证视频时间轴仍能跑通。
"""
from __future__ import annotations

import base64
import json
import struct
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Optional

from server.errors import ExternalServiceError, ServiceNotConfigured

from .tts_client import TTSResult, wav_duration, write_placeholder_wav


def _event_of(message) -> tuple[Optional[str], Optional[dict]]:
    if isinstance(message, bytes):
        return None, None
    try:
        obj = json.loads(message)
    except (TypeError, ValueError):
        return None, None
    if not isinstance(obj, dict):
        return None, None
    header = obj.get("header") if isinstance(obj.get("header"), dict) else {}
    event = header.get("event") or obj.get("event")
    return (str(event) if event else None), obj


def _error_of(obj: dict) -> str:
    payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
    output = payload.get("output") if isinstance(payload.get("output"), dict) else {}
    header = obj.get("header") if isinstance(obj.get("header"), dict) else {}
    for value in (output.get("message"), output.get("error"), payload.get("message"),
                  header.get("error_message"), obj.get("message")):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return json.dumps(obj, ensure_ascii=False)[:300]


class CosyVoiceTTSClient:
    provider = "cosyvoice_ws"

    def __init__(self, ws_url: str, api_key: Optional[str], model: str, voice: str, *,
                 audio_format: str = "wav", sample_rate: int = 22050, volume: int = 50,
                 rate: float = 1.0, pitch: float = 1.0, timeout: float = 60.0,
                 language_hints: tuple = (), instruction: Optional[str] = None,
                 enable_ssml: bool = False):
        self.ws_url = ws_url
        self.api_key = api_key or ""
        self.model = model
        self.tts_model = model          # 与 TTSClient 接口保持一致
        self.voice = voice
        self.audio_format = audio_format
        self.sample_rate = sample_rate
        self.volume = volume
        self.rate = rate
        self.pitch = pitch
        self.timeout = float(timeout)
        self.language_hints = list(language_hints or ())
        self.instruction = instruction
        self.enable_ssml = bool(enable_ssml)

    @property
    def configured(self) -> bool:
        return bool(self.ws_url and self.api_key)

    def _parameters(self, voice: str) -> dict:
        params = {
            "text_type": "PlainText",
            "voice": voice or self.voice,
            "format": self.audio_format,
            "sample_rate": int(self.sample_rate),
            "volume": int(self.volume),
            "rate": float(self.rate),
            "pitch": float(self.pitch),
            "enable_ssml": self.enable_ssml,
        }
        if self.language_hints:
            params["language_hints"] = self.language_hints[:1]
        if self.instruction:
            params["instruction"] = self.instruction
        return params

    def _wrap_pcm(self, data: bytes, path: Path) -> None:
        import wave
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(int(self.sample_rate))
            wf.writeframes(data)

    @staticmethod
    def _normalize_wav_header(data: bytes) -> bytes:
        """流式 WAV 的 data 长度常被写成 0x7fffff9b；按实际字节数修正，否则 duration 会算错。"""
        if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
            return data
        index = data.find(b"data", 12)
        if index == -1 or index + 8 > len(data):
            return data
        buffer = bytearray(data)
        actual = len(buffer) - (index + 8)
        struct.pack_into("<I", buffer, 4, len(buffer) - 8)
        struct.pack_into("<I", buffer, index + 4, actual)
        return bytes(buffer)

    def _convert_to_wav(self, data: bytes, path: Path) -> None:
        """把 mp3/opus 等转成 WAV；wav 直接写并修正流式头。"""
        if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(self._normalize_wav_header(data))
            return
        if self.audio_format == "pcm":
            self._wrap_pcm(data, path)
            return
        suffix = {"mp3": ".mp3", "opus": ".opus"}.get(self.audio_format, ".bin")
        with tempfile.TemporaryDirectory(prefix="pbsc_tts_") as tmp:
            source = Path(tmp) / f"audio{suffix}"
            source.write_bytes(data)
            try:
                import imageio_ffmpeg
                ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
            except Exception as exc:       # noqa: BLE001
                raise ExternalServiceError("CosyVoice 返回非 WAV 音频，且本机找不到 ffmpeg 转码",
                                           service="tts") from exc
            process = subprocess.run(
                [ffmpeg, "-y", "-loglevel", "error", "-i", str(source), "-ac", "1",
                 "-ar", str(self.sample_rate), str(path)],
                capture_output=True, timeout=120)
            if process.returncode != 0 or not path.is_file():
                detail = process.stderr.decode("utf-8", "replace")[-300:]
                raise ExternalServiceError(f"CosyVoice 音频转码失败：{detail}", service="tts")

    def synthesize(self, text: str, out_path: Path, voice: str = "", lang: str = "cn",
                   trace_id: str = "pbsc") -> TTSResult:
        out_path = Path(out_path)
        started = time.perf_counter()
        voice = voice or self.voice
        if not self.configured:
            duration = write_placeholder_wav(text, out_path)
            return TTSResult(out_path, duration, self.provider, True,
                             int((time.perf_counter() - started) * 1000), len(text), voice)

        try:
            from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidStatus
            from websockets.sync.client import connect
        except ImportError as exc:
            raise ServiceNotConfigured("缺少 websockets 依赖，请执行 pip install -r requirements.txt",
                                       details={"service": "tts"}) from exc

        task_id = str(uuid.uuid4())
        headers = {"Authorization": f"bearer {self.api_key}", "X-DashScope-DataInspection": "enable"}
        run_task = {
            "header": {"action": "run-task", "task_id": task_id, "streaming": "duplex"},
            "payload": {
                "task_group": "audio", "task": "tts", "function": "SpeechSynthesizer",
                "model": self.model, "parameters": self._parameters(voice), "input": {},
            },
        }
        continue_task = {
            "header": {"action": "continue-task", "task_id": task_id, "streaming": "duplex"},
            "payload": {"input": {"text": text}},
        }
        finish_task = {
            "header": {"action": "finish-task", "task_id": task_id, "streaming": "duplex"},
            "payload": {"input": {}},
        }
        audio = bytearray()
        deadline = time.monotonic() + self.timeout
        try:
            with connect(self.ws_url, additional_headers=headers, max_size=None,
                         open_timeout=15, close_timeout=5) as ws:
                ws.send(json.dumps(run_task, ensure_ascii=False))
                started_event = False
                while time.monotonic() < deadline:
                    message = ws.recv(timeout=max(0.1, deadline - time.monotonic()))
                    if isinstance(message, bytes):
                        audio.extend(message)
                        continue
                    event, obj = _event_of(message)
                    if event == "task-started":
                        started_event = True
                        break
                    if event in ("task-failed", "task-finished"):
                        raise ExternalServiceError(f"CosyVoice 任务启动失败：{_error_of(obj or {})}",
                                                   service="tts")
                if not started_event:
                    raise ExternalServiceError("CosyVoice 没有返回 task-started", service="tts",
                                               retryable=True)
                ws.send(json.dumps(continue_task, ensure_ascii=False))
                ws.send(json.dumps(finish_task, ensure_ascii=False))
                finished = False
                while time.monotonic() < deadline:
                    message = ws.recv(timeout=max(0.1, deadline - time.monotonic()))
                    if isinstance(message, bytes):
                        audio.extend(message)
                        continue
                    event, obj = _event_of(message)
                    if event == "task-finished":
                        finished = True
                        break
                    if event == "task-failed":
                        raise ExternalServiceError(f"CosyVoice 合成失败：{_error_of(obj or {})}",
                                                   service="tts")
                    # 兼容把音频放在 JSON 里的实现
                    payload = (obj or {}).get("payload") or {}
                    output = payload.get("output") if isinstance(payload.get("output"), dict) else {}
                    for key in ("audio", "audio_data", "data"):
                        value = output.get(key)
                        if isinstance(value, str):
                            try:
                                audio.extend(base64.b64decode(value))
                            except (ValueError, TypeError):
                                pass
                if not finished:
                    raise ExternalServiceError("CosyVoice 合成超时未收到 task-finished",
                                               service="tts", retryable=True)
        except (InvalidStatus, InvalidHandshake) as exc:
            raise ExternalServiceError(f"CosyVoice WebSocket 握手失败：{exc}", service="tts",
                                       details={"url": self.ws_url}) from exc
        except (ConnectionClosed, TimeoutError, OSError) as exc:
            raise ExternalServiceError(f"CosyVoice WebSocket 连接异常：{type(exc).__name__}: {exc}",
                                       service="tts", retryable=True) from exc
        if not audio:
            raise ExternalServiceError("CosyVoice 没有返回音频数据", service="tts", retryable=True)
        self._convert_to_wav(bytes(audio), out_path)
        duration = round(wav_duration(out_path), 3)
        return TTSResult(out_path, duration, self.provider, False,
                         int((time.perf_counter() - started) * 1000), len(text), voice)
