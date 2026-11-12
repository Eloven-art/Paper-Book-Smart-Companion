# -*- coding: utf-8 -*-
"""统一响应格式（共同约定 零.2）。

每个响应都是：
    {
      "status": "ok" | "error",
      "data":   {...} | null,
      "error":  null | {"code": "...", "message": "...", "details": {...}},
      "run_id": "req-20261111T101500-3f9a1c",
      "elapsed_ms": 12,
      "mock": false            # true 表示本次返回的是假接口的固定示例数据
    }
"""
from __future__ import annotations

import secrets
import time
from typing import Any, Optional

from flask import g, jsonify


def new_run_id(prefix: str = "req") -> str:
    return f"{prefix}-{time.strftime('%Y%m%dT%H%M%S')}-{secrets.token_hex(3)}"


def begin_request(mock: bool = False) -> None:
    g.run_id = new_run_id("mock" if mock else "req")
    g.started = time.perf_counter()
    g.mock = mock


def _elapsed_ms() -> int:
    started = getattr(g, "started", None)
    return int((time.perf_counter() - started) * 1000) if started else 0


def ok(data: Any = None, http_status: int = 200):
    body = {"status": "ok", "data": data, "error": None, "run_id": getattr(g, "run_id", new_run_id()),
            "elapsed_ms": _elapsed_ms(), "mock": bool(getattr(g, "mock", False))}
    return jsonify(body), http_status


def fail(code: str, message: str, http_status: int = 500, details: Optional[dict] = None):
    error = {"code": code, "message": message}
    if details:
        error["details"] = details
    body = {"status": "error", "data": None, "error": error, "run_id": getattr(g, "run_id", new_run_id()),
            "elapsed_ms": _elapsed_ms(), "mock": bool(getattr(g, "mock", False))}
    return jsonify(body), http_status
