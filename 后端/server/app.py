# -*- coding: utf-8 -*-
"""Flask 应用工厂。

启动：在仓库根目录执行  python -m server
访问：
    /api/v1/...        真实接口
    /mock/api/v1/...   假接口（固定示例数据）
    /                  如果存在 web/index.html 则托管前端页面
"""
from __future__ import annotations

import logging
from typing import Optional

from flask import Flask, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException

from agents.pipeline import build_pipeline
from configs.settings import Settings, get_settings

from . import api, mock_api
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
    tasks = TaskManager(workers=settings.task_workers, store_dir=settings.tasks_dir)
    app.extensions["pbsc"] = {"settings": settings, "pipeline": pipeline, "tasks": tasks}

    app.register_blueprint(api.bp)
    app.register_blueprint(mock_api.bp)

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
        # 只挡真正的接口路径；前端的 api/service.js、api/voice.js 是静态模块文件，要放行
        if filename.startswith("api/v1/") or filename.startswith("mock/"):
            from werkzeug.exceptions import NotFound
            raise NotFound()
        if web_dir.is_dir():
            return send_from_directory(web_dir, filename)
        return jsonify({"error": "not found"}), 404

    return app
