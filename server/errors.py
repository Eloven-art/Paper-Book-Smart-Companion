# -*- coding: utf-8 -*-
"""统一的业务异常。每个异常带一个稳定的错误码，前端和评测脚本按错误码分支，不要解析中文提示。"""
from __future__ import annotations

from typing import Optional


class AppError(Exception):
    code = "INTERNAL_ERROR"
    http_status = 500
    retryable = False

    def __init__(self, message: str, *, code: Optional[str] = None, http_status: Optional[int] = None,
                 details: Optional[dict] = None, retryable: Optional[bool] = None):
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if http_status is not None:
            self.http_status = http_status
        if retryable is not None:
            self.retryable = retryable
        self.details = details or {}

    def to_dict(self) -> dict:
        body = {"code": self.code, "message": self.message}
        if self.details:
            body["details"] = self.details
        return body


class ValidationError(AppError):
    """请求参数不合法。"""
    code = "INVALID_ARGUMENT"
    http_status = 400


class NotFoundError(AppError):
    code = "NOT_FOUND"
    http_status = 404


class ConflictError(AppError):
    code = "CONFLICT"
    http_status = 409


class UploadRejected(AppError):
    """上传文件没有通过校验。"""
    code = "UPLOAD_REJECTED"
    http_status = 400


class NotImplementedYet(AppError):
    """真实模块还没接入；假接口路径下有同名接口可用。"""
    code = "NOT_IMPLEMENTED"
    http_status = 501


class ServiceNotConfigured(AppError):
    """外部服务的地址或密钥没有配置。"""
    code = "SERVICE_NOT_CONFIGURED"
    http_status = 503


class ExternalServiceError(AppError):
    """外部服务调用失败。retryable 表示值得重试（超时、连接失败、429、5xx）。"""
    code = "EXTERNAL_SERVICE_ERROR"
    http_status = 502

    def __init__(self, message: str, *, service: str = "", status: Optional[int] = None,
                 retryable: bool = False, details: Optional[dict] = None):
        merged = {"service": service}
        if status is not None:
            merged["upstream_status"] = status
        merged.update(details or {})
        super().__init__(message, details=merged, retryable=retryable)
        self.service = service
        self.status = status


class BudgetExceeded(AppError):
    """达到预算上限，拒绝继续调用收费服务。"""
    code = "BUDGET_EXCEEDED"
    http_status = 429


class TaskCancelled(AppError):
    code = "TASK_CANCELLED"
    http_status = 409
