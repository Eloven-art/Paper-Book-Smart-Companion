# -*- coding: utf-8 -*-
"""语音识别节点测试：用假引擎验证标签解析、接口、时长限制和错误，不加载真实模型。

真实模型的识别质量用本地生成的语音手工验收；这里只证明“SenseVoice 输出如何变成接口字段”，
避免测试依赖 230 MB 模型。
"""
import io

from conftest import make_settings, make_wav_bytes
from server.app import create_app

from agents.asr_agent import parse_result

API = "/api/v1"


class FakeEngine:
    def __init__(self, raw="<|zh|><|NEUTRAL|><|Speech|><|withitn|>月亮住在哪里呢？"):
        self.raw = raw
        self.calls = []

    def __call__(self, files, **kwargs):
        self.calls.append((list(files), dict(kwargs)))
        return [self.raw]


def test_parse_result_extracts_tags():
    parsed = parse_result("<|zh|><|HAPPY|><|Speech|><|withitn|>你好，朵朵。")
    assert parsed == {"text": "你好，朵朵。", "language": "zh", "emotion": "HAPPY",
                      "event": "Speech", "textnorm": "withitn"}
    assert parse_result("")["text"] == ""


def test_asr_status_exposes_deployment_state(app):
    status = app.extensions["pbsc"]["pipeline"].asr.status()
    for key in ("engine", "model_dir", "language", "available", "vendor_ready",
                "missing_dependencies", "models_ready", "model_files", "loaded"):
        assert key in status
    assert status["engine"] == "sensevoice-onnx" and status["vendor_ready"] is True


def test_asr_endpoint_transcribes_and_traces(app, client):
    engine = FakeEngine()
    app.extensions["pbsc"]["pipeline"].asr._engine = engine
    resp = client.post(f"{API}/asr", data={
        "audio": (io.BytesIO(make_wav_bytes(0.6)), "ask.wav"),
        "language": "zh", "book_id": "demo_book", "page_no": "1",
    }, content_type="multipart/form-data")
    assert resp.status_code == 200, resp.json
    data = resp.json["data"]
    assert data["text"] == "月亮住在哪里呢？"
    assert data["raw_text"].startswith("<|zh|>")
    assert data["language"] == "zh" and data["emotion"] == "NEUTRAL" and data["event"] == "Speech"
    assert 0.4 < data["duration"] < 0.8 and data["run_id"]
    assert data["audio_bytes"] > 0 and data["audio_format"] == "wav"
    assert engine.calls and engine.calls[0][1]["language"] == "zh"

    run = client.get(f"{API}/runs/{data['run_id']}").json["data"]
    assert run["kind"] == "asr" and run["status"] == "ok"
    assert run["input"]["book_id"] == "demo_book" and run["input"]["page_no"] == 1


def test_asr_rejects_content_mismatch(client):
    resp = client.post(f"{API}/asr", data={"audio": (io.BytesIO(b"not audio"), "x.wav")},
                       content_type="multipart/form-data")
    assert resp.status_code == 415
    assert resp.json["error"]["code"] == "UPLOAD_CONTENT_MISMATCH"


def test_asr_unavailable_returns_explicit_error(app, client, monkeypatch):
    from agents.asr_agent import ASRAgent
    monkeypatch.setattr(ASRAgent, "status", lambda self: {
        "engine": "sensevoice-onnx", "model_dir": "missing", "language": "auto", "available": False,
        "vendor_ready": False, "missing_dependencies": ["onnxruntime"], "models_ready": False,
        "model_files": [], "loaded": False, "error": None,
    })
    resp = client.post(f"{API}/asr", data={"audio": (io.BytesIO(make_wav_bytes(0.3)), "a.wav")},
                       content_type="multipart/form-data")
    assert resp.status_code == 503
    assert resp.json["error"]["code"] == "ASR_UNAVAILABLE"


def test_asr_duration_limit(tmp_path):
    app = create_app(make_settings(tmp_path, PBSC_ASR_MAX_SECONDS="0.1"))
    try:
        app.extensions["pbsc"]["pipeline"].asr._engine = FakeEngine()
        resp = app.test_client().post(f"{API}/asr", data={
            "audio": (io.BytesIO(make_wav_bytes(0.6)), "long.wav"),
        }, content_type="multipart/form-data")
        assert resp.status_code == 413
        assert resp.json["error"]["code"] == "AUDIO_TOO_LONG"
    finally:
        app.extensions["pbsc"]["tasks"].shutdown(wait=True)
