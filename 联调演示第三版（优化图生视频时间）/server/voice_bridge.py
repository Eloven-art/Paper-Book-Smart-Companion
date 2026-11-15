"""把可选音色服务接入主站；只转发约定的五类接口。"""
import requests
from flask import Blueprint, Response, current_app, jsonify, request

bp = Blueprint("voice_bridge", __name__)


@bp.get("/api/voice/status")
@bp.route("/api/voices", methods=["GET", "POST"])
@bp.post("/api/voices/selection")
@bp.post("/api/voices/<voice_id>/speech")
def forward(voice_id=None):
    base = current_app.extensions["pbsc"]["settings"].voice_service_url
    if not base:
        if request.path == "/api/voice/status":
            return jsonify(configured=False, audio_tools=False, integrated=False)
        if request.path == "/api/voices" and request.method == "GET":
            return jsonify(voices=[], selected_voice_id=None)
        return jsonify(error={"code": "VOICE_UNAVAILABLE", "message": "尚未连接音色服务"}), 503
    headers = {k: request.headers[k] for k in ("Content-Type", "Authorization") if k in request.headers}
    try:
        # 固定服务地址由部署者配置，不接受浏览器指定目标地址；禁止自动跟随重定向。
        upstream = requests.request(request.method, base.rstrip("/") + request.path,
                                    headers=headers, data=request.get_data(), timeout=(5, 135),
                                    allow_redirects=False)
    except requests.RequestException:
        return jsonify(error={"code": "VOICE_UNAVAILABLE", "message": "音色服务暂时无法连接，请稍后重试"}), 503
    return Response(upstream.content, status=upstream.status_code,
                    headers={"Content-Type": upstream.headers.get("Content-Type", "application/octet-stream"),
                             "Cache-Control": "no-store"})
