"""主站静态资源、触控和音色接入边界。"""
from dataclasses import replace

from server.app import create_app


def test_frontend_assets_and_private_paths(client):
    for path in ("/", "/app.js", "/style.css", "/api/service.js", "/api/voice.js", "/ui/voice-panel.js"):
        assert client.get(path).status_code == 200, path
    for path in ("/.env", "/.env.example", "/backend/server.py", "/work/voice-data/voices.sqlite3", "/originals/tts_v2.html"):
        assert client.get(path).status_code == 404, path


def test_first_start_installs_sample_without_overwriting(settings):
    settings = replace(settings, install_sample=True)
    for title in ("朵朵找月亮", "保留用户改动"):
        app = create_app(settings)
        store = app.extensions["pbsc"]["pipeline"].store
        book = store.load("demo_book")
        assert book["title"] == title
        book["title"] = "保留用户改动"
        store.save(book)
        app.extensions["pbsc"]["tasks"].shutdown(wait=True)


def test_real_touch_requires_video_annotations(app, client, sample_dir):
    store = app.extensions["pbsc"]["pipeline"].store
    store.install_sample(sample_dir)
    payload = dict(book_id="demo_book", page_no=1, x=980, y=150)
    result = client.post("/api/v1/touch/identify", json=payload).json["data"]
    assert result["hit"] and result["object"]["name"] == "月亮"
    result = client.post("/api/v1/touch/identify", json={**payload, "play_time": 0}).json["data"]
    assert not result["hit"] and result["source"] == "keyframe"
    for patch in ({"x": -1}, {"play_time": -1}, {"coord_space": "bad"}):
        assert client.post("/api/v1/touch/identify", json={**payload, **patch}).status_code == 400


def test_voice_absent_is_explicit(client):
    assert client.get("/api/voice/status").json["configured"] is False
    assert client.get("/api/voices").json["voices"] == []
    assert client.post("/api/voices").status_code == 503


def test_voice_bridge_preserves_audio_and_authorization(settings, monkeypatch):
    from server import voice_bridge
    calls = []

    def upstream(method, url, **kwargs):
        calls.append((method, url, kwargs))
        class Result:
            content = b"audio-fixture"
            status_code = 200
            headers = {"Content-Type": "audio/mpeg"}
        return Result()

    monkeypatch.setattr(voice_bridge.requests, "request", upstream)
    app = create_app(replace(settings, voice_service_url="http://127.0.0.1:8001"))
    try:
        response = app.test_client().post("/api/voices/test-id/speech", json={"text": "你好"},
                                          headers={"Authorization": "Bearer test-token"})
        assert response.data == b"audio-fixture" and response.mimetype == "audio/mpeg"
        assert calls[0][1] == "http://127.0.0.1:8001/api/voices/test-id/speech"
        assert calls[0][2]["headers"]["Authorization"] == "Bearer test-token"
        assert calls[0][2]["allow_redirects"] is False
    finally:
        app.extensions["pbsc"]["tasks"].shutdown(wait=True)
