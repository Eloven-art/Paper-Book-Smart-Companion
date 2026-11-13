# -*- coding: utf-8 -*-
"""视觉语言模型（VLM）客户端：把绘本页图/视频帧发给成熟的 VLM 服务，取回画面理解。

协议：OpenAI 兼容的 chat/completions + base64 图片（message.content 为块数组）。
地址/密钥/模型来自 IMAGE_ANALYSIS_URL / IMAGE_API_KEY / IMAGE_MODEL，因此可接
智谱 GLM-4V、阿里云百炼 Qwen-VL、vLLM/SGLang 自部署等任何兼容服务，代码不绑定厂商。

相对参考代码 gemini_analyze.py 的改进：区分可重试/不可重试错误、返回 token 用量、
图片自动缩放（省 token）、不打印正文。
"""
from __future__ import annotations

import base64
import io
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Union

import requests

from server.errors import ExternalServiceError, ServiceNotConfigured


@dataclass
class VlmResult:
    text: str
    model: str
    elapsed_ms: int
    usage: dict = field(default_factory=dict)      # prompt_tokens / completion_tokens / total_tokens
    finish_reason: Optional[str] = None


class VlmClient:
    def __init__(self, url, api_key, model, timeout=120.0, max_image_side=1280, session=None):
        self.url = url
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_image_side = max_image_side          # 图片长边上限，压小省 token
        self._session = session or requests.Session()

    @property
    def configured(self) -> bool:
        return bool(self.url)

    # ---------- 图片编码（读取 → 缩放 → base64 PNG）----------
    def encode_image(self, image: Union[bytes, Path, str]) -> str:
        from PIL import Image
        if isinstance(image, (str, Path)):
            image = Path(image).read_bytes()
        with Image.open(io.BytesIO(image)) as im:
            im = im.convert("RGB")
            w, h = im.size
            scale = self.max_image_side / max(w, h)
            if scale < 1:
                im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))))
            buf = io.BytesIO()
            im.save(buf, format="PNG")
            data = buf.getvalue()
        return "data:image/png;base64," + base64.b64encode(data).decode("ascii")

    # ---------- 调用（厂商差异全部收在这里）----------
    def analyze(self, image, prompt: str, system: str = "", *, temperature: float = 0.1,
                max_tokens: Optional[int] = None) -> VlmResult:
        if not self.configured:
            raise ServiceNotConfigured("VLM 服务未配置（IMAGE_ANALYSIS_URL）", details={"service": "vlm"})
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": self.encode_image(image)}},
        ]})
        payload = {"model": self.model, "temperature": temperature, "stream": False, "messages": messages}
        if max_tokens:
            payload["max_tokens"] = max_tokens
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        started = time.perf_counter()
        try:
            resp = self._session.post(self.url, headers=headers,
                                      data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                      timeout=self.timeout)
        except requests.Timeout as exc:
            raise ExternalServiceError(f"VLM 请求超时（{self.timeout:.0f} 秒）", service="vlm",
                                       retryable=True) from exc
        except requests.RequestException as exc:
            raise ExternalServiceError(f"VLM 请求失败：{type(exc).__name__}", service="vlm",
                                       retryable=isinstance(exc, requests.ConnectionError)) from exc
        elapsed = int((time.perf_counter() - started) * 1000)

        if resp.status_code >= 400:
            raise ExternalServiceError(f"VLM 返回 HTTP {resp.status_code}", service="vlm",
                                       status=resp.status_code,
                                       retryable=resp.status_code in (408, 409, 429) or resp.status_code >= 500,
                                       details={"body": resp.text[:300]})
        try:
            body = resp.json()
        except ValueError as exc:
            raise ExternalServiceError("VLM 返回的不是 JSON", service="vlm", status=resp.status_code,
                                       details={"body": resp.text[:300]}) from exc
        if isinstance(body, dict) and body.get("error"):
            raise ExternalServiceError(f"VLM 返回错误：{str(body['error'])[:200]}", service="vlm")
        try:
            choice = body["choices"][0]
            text = choice["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ExternalServiceError("VLM 返回格式不符（缺少 choices[0].message.content）", service="vlm",
                                       details={"body": json.dumps(body, ensure_ascii=False)[:300]}) from exc
        if isinstance(text, list):                     # 部分服务把多模态回复也组织成 content 列表
            text = "".join(p.get("text", "") for p in text if isinstance(p, dict))
        if not isinstance(text, str) or not text.strip():
            raise ExternalServiceError("VLM 返回了空内容", service="vlm", retryable=True)
        usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
        return VlmResult(text=text, model=body.get("model") or self.model or "", elapsed_ms=elapsed,
                         usage={k: usage[k] for k in ("prompt_tokens", "completion_tokens", "total_tokens")
                                if isinstance(usage.get(k), int)},
                         finish_reason=choice.get("finish_reason"))
