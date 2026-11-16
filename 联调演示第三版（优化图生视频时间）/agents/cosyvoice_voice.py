# -*- coding: utf-8 -*-
"""CosyVoice 音色复刻管理：上传参考音频、查询列表/详情、更新、删除。

调用官方 DashScope SDK 的 `client.audio.voices.*`；本地音频以 Base64 Data URI
传给 `create(audio=...)`，不需要公网 URL。更新接口 SDK 只提供 url 参数，这里用
HTTP customization 接口传 Data URI。

密钥只从环境变量读取；返回结果里只有 voice_id/状态，不回显 Key。
"""
from __future__ import annotations

import base64
from typing import Iterable, Optional

import requests

from server.errors import ExternalServiceError, ServiceNotConfigured


class CosyVoiceVoiceManager:
    def __init__(self, api_key: Optional[str], workspace: Optional[str],
                 region: str = "cn-beijing", target_model: str = "cosyvoice-v3-plus",
                 timeout: float = 120.0):
        self.api_key = api_key or ""
        self.workspace = workspace or None
        self.region = region or "cn-beijing"
        self.target_model = target_model
        self.timeout = float(timeout)
        self._client = None

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def status(self) -> dict:
        return {"configured": self.configured, "target_model": self.target_model,
                "workspace": bool(self.workspace), "region": self.region}

    def _get_client(self):
        if self._client is not None:
            return self._client
        if not self.api_key:
            raise ServiceNotConfigured("CosyVoice 音色服务未配置（COSYVOICE_API_KEY / DASHSCOPE_API_KEY）",
                                       details={"service": "tts"})
        try:
            import dashscope
        except ImportError as exc:
            raise ServiceNotConfigured("未安装 dashscope SDK，请执行 pip install -r requirements.txt",
                                       details={"service": "tts"}) from exc
        self._client = dashscope.Client(api_key=self.api_key, workspace=self.workspace,
                                        region=self.region, timeout=self.timeout)
        return self._client

    @staticmethod
    def _data_uri(audio: bytes, mime: str) -> str:
        return f"data:{mime or 'audio/wav'};base64," + base64.b64encode(audio).decode("ascii")

    @staticmethod
    def _dump(value) -> dict:
        if hasattr(value, "model_dump"):
            return value.model_dump()
        return dict(value) if isinstance(value, dict) else {}

    _VIEW_KEYS = ("voice_id", "status", "target_model", "preview_audio", "request_id", "usage")

    @classmethod
    def _voice_view(cls, value) -> dict:
        data = cls._dump(value)
        view = {key: data.get(key) for key in cls._VIEW_KEYS if key in data}
        view.setdefault("status", "DEPLOYING")
        return view

    def create_voice(self, audio: bytes, mime: str, prefix: str,
                     language_hints: Optional[Iterable[str]] = None) -> dict:
        try:
            voice = self._get_client().audio.voices.create(
                target_model=self.target_model, prefix=prefix,
                audio=self._data_uri(audio, mime),
                language_hints=list(language_hints or ["zh"]))
        except Exception as exc:       # noqa: BLE001
            raise ExternalServiceError(f"CosyVoice 音色创建失败：{exc}", service="tts",
                                       details={"provider": "cosyvoice", "target_model": self.target_model,
                                                "status": getattr(exc, "status_code", None)}) from exc
        return self._voice_view(voice)

    def list_voices(self, prefix: Optional[str] = None, page_index: int = 0,
                    page_size: int = 20) -> dict:
        try:
            result = self._get_client().audio.voices.list(
                prefix=prefix or None, page_index=page_index, page_size=page_size)
        except Exception as exc:       # noqa: BLE001
            raise ExternalServiceError(f"CosyVoice 音色列表读取失败：{exc}", service="tts",
                                       details={"provider": "cosyvoice"}) from exc
        data = self._dump(result)
        voices = []
        for item in data.get("voices") or []:
            if isinstance(item, dict):
                voices.append({k: item.get(k) for k in self._VIEW_KEYS if k in item})
        return {"voices": voices, "total": data.get("total"), "request_id": data.get("request_id")}

    def get_voice(self, voice_id: str) -> dict:
        try:
            result = self._get_client().audio.voices.get(voice_id)
        except Exception as exc:       # noqa: BLE001
            raise ExternalServiceError(f"CosyVoice 音色查询失败：{exc}", service="tts",
                                       details={"provider": "cosyvoice", "voice_id": voice_id}) from exc
        return self._voice_view(result)

    def delete_voice(self, voice_id: str) -> dict:
        try:
            result = self._get_client().audio.voices.delete(voice_id)
        except Exception as exc:       # noqa: BLE001
            raise ExternalServiceError(f"CosyVoice 音色删除失败：{exc}", service="tts",
                                       details={"provider": "cosyvoice", "voice_id": voice_id}) from exc
        data = self._dump(result)
        return {"request_id": data.get("request_id"), "status_code": data.get("status_code")}

    def update_voice(self, voice_id: str, audio: bytes, mime: str) -> dict:
        """替换参考音频，保留 voice_id。SDK update 只收 url，所以用 HTTP 传 Data URI。"""
        host = (f"https://{self.workspace}.{self.region}.maas.aliyuncs.com"
                if self.workspace else "https://dashscope.aliyuncs.com")
        url = f"{host}/api/v1/services/audio/tts/customization"
        payload = {"model": "voice-enrollment",
                   "input": {"action": "update_voice", "voice_id": voice_id,
                             "audio": self._data_uri(audio, mime)}}
        try:
            response = requests.post(url, headers={"Authorization": f"Bearer {self.api_key}",
                                                   "Content-Type": "application/json"},
                                     json=payload, timeout=self.timeout)
        except requests.RequestException as exc:
            raise ExternalServiceError(f"CosyVoice 音色更新请求失败：{type(exc).__name__}",
                                       service="tts", retryable=True) from exc
        if response.status_code >= 400:
            raise ExternalServiceError(f"CosyVoice 音色更新返回 HTTP {response.status_code}",
                                       service="tts", status=response.status_code,
                                       retryable=response.status_code >= 500,
                                       details={"body": response.text[:300]})
        try:
            body = response.json()
        except ValueError as exc:
            raise ExternalServiceError("CosyVoice 音色更新返回的不是 JSON", service="tts") from exc
        if isinstance(body, dict) and body.get("error"):
            raise ExternalServiceError(f"CosyVoice 音色更新失败：{str(body['error'])[:200]}",
                                       service="tts")
        return body
