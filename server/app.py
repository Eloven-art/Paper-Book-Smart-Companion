# -*- coding: utf-8 -*-
"""Flask 应用工厂。

启动：在仓库根目录执行  python -m server
访问：
    /api/v1/...        真实接口
    /mock/api/v1/...   假接口（固定示例数据）
    /                  托管前端页面（默认 前端/ 目录，可用 PBSC_WEB_DIR 指定）
"""
from __future__ import annotations

import logging
from typing import Optional

from flask import Flask, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException

from agents.pipeline import build_pipeline
from configs.settings import Settings, get_settings

from . import api, mock_api, voice_bridge
from .envelope import begin_request, fail
from .errors import AppError
from .task_queue import TaskManager

log = logging.getLogger("pbsc.app")
VERSION = "0.1.0"


def create_app(settings: Optional[Settings] = None) -> Flask:
    settings = settings or get_settings()
    settings.ensure_dirs()
    web_dir = settings.web_dir
    app = Flask(__name__, static_folder=None)
    app.json.ensure_ascii = False
    # 整个请求体的上限：单文件上限 × 页数上限，另留 1 MB 给表单字段
    app.config["MAX_CONTENT_LENGTH"] = int(settings.max_upload_mb * 1024 * 1024 * settings.max_pages_per_upload) + (1 << 20)

    pipeline = build_pipeline(settings)
    sample = settings.samples_dir / "demo_book"
    if settings.install_sample and not pipeline.store.list_ids() and (sample / "book.json").is_file():
        # 首次启动时放入自制示例绘本，打开页面就有书可读（真实链路会重新生成旁白和视频）
        pipeline.store.install_sample(sample)
    tasks = TaskManager(workers=settings.task_workers, store_dir=settings.tasks_dir)
    app.extensions["pbsc"] = {"settings": settings, "pipeline": pipeline, "tasks": tasks}

    app.register_blueprint(api.bp)
    app.register_blueprint(mock_api.bp)
    app.register_blueprint(voice_bridge.bp)

    def is_api_path() -> bool:
        return request.path.startswith(("/api/", "/mock/api/"))

    @app.errorhandler(AppError)
    def handle_app_error(exc: AppError):
        return fail(exc.code, exc.message, exc.http_status, exc.details)

    @app.errorhandler(HTTPException)
    def handle_http_error(exc: HTTPException):
        if not is_api_path():
            return exc
        codes = {400: "INVALID_ARGUMENT", 404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED", 413: "UPLOAD_TOO_LARGE",
                 415: "UNSUPPORTED_MEDIA_TYPE"}
        messages = {404: "接口不存在", 405: "请求方法不被允许", 413: "请求体超过大小上限"}
        return fail(codes.get(exc.code, "HTTP_ERROR"), messages.get(exc.code, exc.description or "请求错误"),
                    exc.code or 500)

    @app.errorhandler(Exception)
    def handle_unexpected(exc: Exception):
        log.exception("未处理的异常：%s %s", request.method, request.path)
        if not is_api_path():
            return jsonify({"error": "internal error"}), 500
        # 不把内部细节返回给调用方；详情看服务端日志，按 run_id 对应
        return fail("INTERNAL_ERROR", "服务内部错误，请凭 run_id 查看服务端日志", 500)

    @app.before_request
    def ensure_envelope_context():
        # 蓝图之外的 /api 路径（比如写错的地址）也要有 run_id 和计时
        if is_api_path() and request.blueprint is None:
            begin_request(mock=request.path.startswith("/mock/"))
        if request.method == "OPTIONS" and is_api_path():
            return "", 204
        return None

    @app.after_request
    def add_cors(resp):
        if is_api_path():
            resp.headers["Access-Control-Allow-Origin"] = settings.cors_origins
            resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
            resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        return resp

    @app.get("/")
    def index():
        if (web_dir / "index.html").is_file():
            return send_from_directory(web_dir, "index.html")
        return jsonify({"service": "paper-book-smart-companion", "version": VERSION,
                        "api": "/api/v1/health", "mock_api": "/mock/api/v1/health",
                        "docs": "docs/接口约定_v0.1.md"})

    @app.get("/<path:filename>")
    def web_files(filename: str):
        # 仅发布页面资源；前端目录还包含后端源码、环境文件和声音数据库。
        parts = filename.split("/")
        allowed = filename in {"app.js", "style.css", "api/service.js", "api/voice.js", "ui/voice-panel.js"}
        allowed = allowed or (parts[0] == "assets" and all(not p.startswith(".") for p in parts))
        if allowed and web_dir.is_dir():
            return send_from_directory(web_dir, filename)
        from werkzeug.exceptions import NotFound
        raise NotFound()

    return app
