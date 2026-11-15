# -*- coding: utf-8 -*-
"""音色复刻管理测试：上传参考音频、列表/详情、选择、删除和 /tts 使用选中音色。"""
import io
import struct
import time
import wave

from conftest import make_settings

from agents.tts_client import TTSResult
from server.app import create_app


def wav_bytes(seconds=6.0, rate=16000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(struct.pack("<h", 0) * int(seconds * rate))
    return buf.getvalue()


class FakeVoiceManager:
    def status(self):
        return {"configured": True, "target_model": "cosyvoice-v3-plus", "workspace": True, "region": "cn-beijing"}

    def create_voice(self, audio, mime, prefix, language_hints=None):
        self.created = {"bytes": len(audio), "mime": mime, "prefix": prefix, "hints": language_hints}
        return {"voice_id": "cosyvoice-v3-plus-pbsc-test", "status": "DEPLOYING",
                "target_model": "cosyvoice-v3-plus"}

    def list_voices(self, prefix=None, page_index=0, page_size=20):
        return {"voices": [{"voice_id": "cosyvoice-v3-plus-pbsc-test", "status": "OK"}]}

    def get_voice(self, voice_id):
        return {"voice_id": voice_id, "status": "OK"}

    def delete_voice(self, voice_id):
        return {}

    def update_voice(self, voice_id, audio, mime):
        return {}


class FakeTTS:
    provider = "cosyvoice_ws"
    tts_model = "cosyvoice-v3-plus"
    voice = "longanyang"
    configured = True

    def __init__(self):
        self.seen = []

    def synthesize(self, text, out_path, voice="", lang="cn", trace_id="x"):
        self.seen.append((text, voice))
        import wave as _wave
        duration = 0.5
        with _wave.open(str(out_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(b"\x00\x00" * int(duration * 16000))
        return TTSResult(out_path, duration, self.provider, False, 1, len(text), voice or self.voice)


def test_voice_upload_list_select_delete(tmp_path):
    settings = make_settings(tmp_path, PBSC_TTS_PROVIDER="cosyvoice", COSYVOICE_API_KEY="test-key")
    app = create_app(settings)
    try:
        pipeline = app.extensions["pbsc"]["pipeline"]
        fake = FakeVoiceManager()
        pipeline.voices = fake
        client = app.test_client()

        created = client.post("/api/v1/voices/cosyvoice", data={
            "audio": (io.BytesIO(wav_bytes(6)), "ref.wav"), "prefix": "pbsc",
        }, content_type="multipart/form-data")
        assert created.status_code == 202, created.json
        voice_id = created.json["data"]["voice_id"]
        assert voice_id.endswith("pbsc-test") and fake.created["prefix"] == "pbsc"

        listed = client.get("/api/v1/voices/cosyvoice").json["data"]
        assert listed["voices"][0]["voice_id"] == voice_id
        assert client.get(f"/api/v1/voices/cosyvoice/{voice_id}").json["data"]["status"] == "OK"

        selected = client.post("/api/v1/voices/cosyvoice/selection", json={"voice_id": voice_id})
        assert selected.json["data"]["voice_id"] == voice_id
        assert client.get("/api/v1/voices/cosyvoice/selection").json["data"]["voice_id"] == voice_id

        assert client.delete(f"/api/v1/voices/cosyvoice/{voice_id}").status_code == 200
    finally:
        app.extensions["pbsc"]["tasks"].shutdown(wait=True)


def test_voice_upload_rejects_short_audio(tmp_path):
    settings = make_settings(tmp_path, PBSC_TTS_PROVIDER="cosyvoice", COSYVOICE_API_KEY="test-key")
    app = create_app(settings)
    try:
        app.extensions["pbsc"]["pipeline"].voices = FakeVoiceManager()
        resp = app.test_client().post("/api/v1/voices/cosyvoice", data={
            "audio": (io.BytesIO(wav_bytes(2)), "short.wav"),
        }, content_type="multipart/form-data")
        assert resp.status_code == 400
        assert resp.json["error"]["code"] == "AUDIO_TOO_SHORT_OR_LONG"
    finally:
        app.extensions["pbsc"]["tasks"].shutdown(wait=True)


def test_tts_uses_selected_voice(tmp_path):
    settings = make_settings(tmp_path, PBSC_TTS_PROVIDER="cosyvoice", COSYVOICE_API_KEY="test-key")
    app = create_app(settings)
    try:
        pipeline = app.extensions["pbsc"]["pipeline"]
        fake = FakeTTS()
        pipeline.tts = fake
        pipeline.video.tts = fake
        client = app.test_client()
        client.post("/api/v1/voices/cosyvoice/selection",
                    json={"voice_id": "cosyvoice-v3-plus-myvoice"})
        resp = client.post("/api/v1/tts", json={"text": "测试选中音色"})
        assert resp.status_code == 200, resp.json
        assert resp.json["data"]["voice"] == "cosyvoice-v3-plus-myvoice"
        assert fake.seen[-1][1] == "cosyvoice-v3-plus-myvoice"
    finally:
        app.extensions["pbsc"]["tasks"].shutdown(wait=True)
