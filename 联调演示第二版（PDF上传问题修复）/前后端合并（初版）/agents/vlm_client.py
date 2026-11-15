# -*- coding: utf-8 -*-
"""VLM（视觉语言模型）HTTP 客户端。

支持两种常见调用方式，通过 PBSC_VLM_PROTOCOL 选择：

1. openai_chat（默认）：OpenAI 兼容的 /chat/completions，多模态消息里用 image_url
   传 data URI。Qwen-VL、通义千问兼容模式、vLLM 等都能用。
2. custom_json：POST 一个 JSON，字段为 image / prompt / page_no / image_size / model，
   直接返回 JSON 或 {"result": ...} / {"data": ...}。适合同学自己写的服务。

密钥只从环境变量读取；错误会转成 ExternalServiceError，调用方决定重试与降级。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Optional

import requests

from server.errors import ExternalServiceError, ServiceNotConfigured


@dataclass
class VLMResult:
    text: str
    model: str
    elapsed_ms: int
    usage: dict = field(default_factory=dict)


def _extract_openai_content(body: dict) -> str:
    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ExternalServiceError(
            "VLM 返回格式不符（缺少 choices[0].message.content）", service="vlm",
            details={"body": json.dumps(body, ensure_ascii=False)[:300]}) from exc
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict):
                if isinstance(part.get("text"), str):
                    parts.append(part["text"])
                elif isinstance(part.get("content"), str):
                    parts.append(part["content"])
        if parts:
            return "\n".join(parts)
    raise ExternalServiceError("VLM 返回了空的文本内容", service="vlm", retryable=True)


class VLMClient:
    def __init__(self, url: Optional[str], api_key: Optional[str], model: Optional[str], *,
                 protocol: str = "openai_chat", timeout: float = 120.0, temperature: float = 0.0,
                 session: Optional[requests.Session] = None):
        self.url = url
        self.api_key = api_key or ""
        self.model = model
        self.protocol = protocol
        self.timeout = timeout
        self.temperature = temperature
        self._session = session or requests.Session()

    @property
    def configured(self) -> bool:
        return bool(self.url)

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _post(self, payload: dict) -> requests.Response:
        try:
            response = self._session.post(self.url, headers=self._headers(),
                                          data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                          timeout=self.timeout)
        except requests.Timeout as exc:
            raise ExternalServiceError(f"VLM 请求超时（{self.timeout:.0f} 秒）", service="vlm",
                                       retryable=True) from exc
        except requests.RequestException as exc:
            raise ExternalServiceError(f"VLM 请求失败：{type(exc).__name__}", service="vlm",
                                       retryable=isinstance(exc, requests.ConnectionError)) from exc
        if response.status_code >= 400:
            raise ExternalServiceError(f"VLM 返回 HTTP {response.status_code}", service="vlm",
                                       status=response.status_code,
                                       retryable=response.status_code in (408, 409, 429) or response.status_code >= 500,
                                       details={"body": response.text[:300]})
        return response

    def analyze(self, image_data_uri: str, system_prompt: str, user_prompt: str, *,
                page_no: int, image_size: dict) -> VLMResult:
        if not self.configured:
            raise ServiceNotConfigured("VLM 服务未配置（VLM_API_URL）", details={"service": "vlm"})
        started = time.perf_counter()
        if self.protocol == "openai_chat":
            payload = {
                "model": self.model,
                "temperature": self.temperature,
                "stream": False,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": [
                        {"type": "text", "text": user_prompt},
                        {"type": "image_url", "image_url": {"url": image_data_uri}},
                    ]},
                ],
            }
            response = self._post(payload)
            try:
                body = response.json()
            except ValueError as exc:
                raise ExternalServiceError("VLM 返回的不是 JSON", service="vlm",
                                           details={"body": response.text[:300]}) from exc
            if isinstance(body, dict) and body.get("error"):
                raise ExternalServiceError(f"VLM 返回错误：{str(body['error'])[:200]}", service="vlm")
            text = _extract_openai_content(body)
            usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
            usage = {k: usage[k] for k in ("prompt_tokens", "completion_tokens", "total_tokens")
                     if isinstance(usage.get(k), int)}
            model = body.get("model") or self.model or ""
        elif self.protocol == "custom_json":
            payload = {
                "model": self.model,
                "image": image_data_uri,
                "prompt": user_prompt,
                "system": system_prompt,
                "page_no": page_no,
                "image_size": image_size,
            }
            response = self._post(payload)
            try:
                body = response.json()
            except ValueError:
                # 有些自建服务直接返回 JSON 文本；交给上层提取。
                text = response.text
                body = None
            if isinstance(body, dict) and body.get("error"):
                raise ExternalServiceError(f"VLM 返回错误：{str(body['error'])[:200]}", service="vlm")
            if body is not None:
                if isinstance(body, str):
                    text = body
                elif isinstance(body, dict) and isinstance(body.get("result"), (dict, list, str)):
                    value = body["result"]
                    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
                elif isinstance(body, dict) and isinstance(body.get("data"), (dict, list, str)):
                    value = body["data"]
                    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
                else:
                    text = json.dumps(body, ensure_ascii=False)
            usage = {}
            model = self.model or ""
        else:
            raise ExternalServiceError(f"不支持的 VLM 协议：{self.protocol}", service="vlm")

        if not isinstance(text, str) or not text.strip():
            raise ExternalServiceError("VLM 返回了空内容", service="vlm", retryable=True)
        return VLMResult(text=text, model=model, elapsed_ms=int((time.perf_counter() - started) * 1000),
                         usage=usage)
