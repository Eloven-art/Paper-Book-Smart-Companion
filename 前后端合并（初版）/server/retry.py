# -*- coding: utf-8 -*-
"""带退避的重试。只重试“再试一次可能成功”的错误：超时、连接失败、上游 429/5xx。"""
from __future__ import annotations

import logging
import random
import time
from typing import Callable, Optional, TypeVar

import requests

from .errors import AppError

T = TypeVar("T")
log = logging.getLogger("pbsc.retry")


def is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, AppError):
        return exc.retryable
    return isinstance(exc, (requests.Timeout, requests.ConnectionError, TimeoutError, ConnectionError))


def call_with_retry(fn: Callable[[], T], *, attempts: int = 3, base_delay: float = 1.0, max_delay: float = 15.0,
                    should_retry: Callable[[BaseException], bool] = is_retryable,
                    on_retry: Optional[Callable[[int, BaseException, float], None]] = None,
                    sleep: Callable[[float], None] = time.sleep, label: str = "") -> T:
    """执行 fn，失败且可重试时按 base_delay * 2^(n-1) 加随机抖动等待后重试，最多 attempts 次。

    on_retry(第几次失败, 异常, 等待秒数) 在每次重试前调用，可用来写日志或更新任务进度。
    """
    attempts = max(1, attempts)
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:
            if attempt >= attempts or not should_retry(exc):
                raise
            delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
            delay *= 0.75 + random.random() * 0.5
            log.warning("%s 第 %d/%d 次失败：%s；%.1f 秒后重试", label or "调用", attempt, attempts, exc, delay)
            if on_retry:
                on_retry(attempt, exc, delay)
            sleep(delay)
    raise RuntimeError("unreachable")  # pragma: no cover
