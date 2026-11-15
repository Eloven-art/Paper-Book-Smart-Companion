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


class OCRUnavailable(AppError):
    """本地 OCR 模块未就绪：源码、依赖或模型文件缺失。"""
    code = "OCR_UNAVAILABLE"
    http_status = 503


class OCRFailed(AppError):
    """本地 OCR 推理失败（模型损坏、图片异常等）。"""
    code = "OCR_FAILED"
    http_status = 500


class ASRUnavailable(AppError):
    """本地语音识别模块未就绪：运行时、依赖或模型文件缺失。"""
    code = "ASR_UNAVAILABLE"
    http_status = 503


class ASRFailed(AppError):
    """本地语音识别失败（音频损坏、转码失败、模型异常等）。"""
    code = "ASR_FAILED"
    http_status = 500


class VLMUnavailable(AppError):
    """VLM 服务未就绪：地址、鉴权或依赖缺失。"""
    code = "VLM_UNAVAILABLE"
    http_status = 503


class VLMFailed(AppError):
    """VLM 调用失败或返回结果无法校验。"""
    code = "VLM_FAILED"
    http_status = 502


class VoiceUnavailable(AppError):
    """音色复刻服务未配置或未就绪。"""
    code = "VOICE_UNAVAILABLE"
    http_status = 503


class VoiceFailed(AppError):
    """音色创建、查询、更新或删除失败。"""
    code = "VOICE_FAILED"
    http_status = 502


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
