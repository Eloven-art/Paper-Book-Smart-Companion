# -*- coding: utf-8 -*-
"""大模型文本接口客户端。

沿用参考代码（参考代码/text.py、text_agent_base.py）的调用方式：向一个 OpenAI 兼容的
chat/completions 地址发 POST，Authorization: Bearer <key>，请求体含 model / temperature /
stop / messages。地址、密钥、模型名来自环境变量 SIMPLE_STORY_WRITER_URL / _API_KEY / _MODEL，
因此可以接 DeepSeek、Qwen 等任何兼容服务，代码里不绑定具体厂商。

相对参考代码的改动：
- 失败时抛出带错误码的异常，区分“值得重试”（超时、429、5xx）和“不必重试”（4xx、返回格式不对）；
- 返回 token 用量（服务端提供时），供费用台账记录；
- 不打印正文，日志由调用方统一写入运行记录。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Optional

import requests

from server.errors import ExternalServiceError, ServiceNotConfigured

DEFAULT_STOP = ["<|endoftext|>", "<|im_end|>", "<|im_start|>"]


@dataclass
class ChatResult:
    text: str
    model: str
    elapsed_ms: int
    usage: dict = field(default_factory=dict)      # prompt_tokens / completion_tokens（服务端提供时）
    finish_reason: Optional[str] = None


class ChatClient:
    def __init__(self, url: Optional[str], api_key: Optional[str], model: Optional[str], timeout: float = 120.0,
                 session: Optional[requests.Session] = None):
        self.url = url
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self._session = session or requests.Session()

    @property
    def configured(self) -> bool:
        return bool(self.url)

    def chat(self, messages: list[dict], *, temperature: float = 0.3, max_tokens: Optional[int] = None,
             stop: Optional[list[str]] = None) -> ChatResult:
        if not self.configured:
            raise ServiceNotConfigured("大模型服务未配置（SIMPLE_STORY_WRITER_URL）", details={"service": "llm"})
        payload = {"model": self.model, "temperature": temperature, "stream": False,
                   "stop": DEFAULT_STOP if stop is None else stop, "messages": messages}
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
            raise ExternalServiceError(f"大模型请求超时（{self.timeout:.0f} 秒）", service="llm",
                                       retryable=True) from exc
        except requests.RequestException as exc:
            raise ExternalServiceError(f"大模型请求失败：{type(exc).__name__}", service="llm",
                                       retryable=isinstance(exc, requests.ConnectionError)) from exc
        elapsed = int((time.perf_counter() - started) * 1000)

        if resp.status_code >= 400:
            raise ExternalServiceError(f"大模型返回 HTTP {resp.status_code}", service="llm", status=resp.status_code,
                                       retryable=resp.status_code in (408, 409, 429) or resp.status_code >= 500,
                                       details={"body": resp.text[:300]})
        try:
            body = resp.json()
        except ValueError as exc:
            raise ExternalServiceError("大模型返回的不是 JSON", service="llm", status=resp.status_code,
                                       details={"body": resp.text[:300]}) from exc
        if isinstance(body, dict) and body.get("error"):
            raise ExternalServiceError(f"大模型返回错误：{str(body['error'])[:200]}", service="llm")
        try:
            choice = body["choices"][0]
            text = choice["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ExternalServiceError("大模型返回格式不符（缺少 choices[0].message.content）", service="llm",
                                       details={"body": json.dumps(body, ensure_ascii=False)[:300]}) from exc
        if not isinstance(text, str) or not text.strip():
            raise ExternalServiceError("大模型返回了空内容", service="llm", retryable=True)
        usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
        return ChatResult(text=text, model=body.get("model") or self.model or "", elapsed_ms=elapsed,
                          usage={k: usage[k] for k in ("prompt_tokens", "completion_tokens", "total_tokens")
                                 if isinstance(usage.get(k), int)},
                          finish_reason=choice.get("finish_reason"))
