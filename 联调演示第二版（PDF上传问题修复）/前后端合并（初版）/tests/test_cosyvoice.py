# -*- coding: utf-8 -*-
"""CosyVoice 客户端测试：流式 WAV 头修正、provider 选择和未配置时的占位音频。"""
import io
import struct
import wave

from conftest import make_settings

from agents.tts_client import TTSResult, build_tts_client, wav_duration, write_placeholder_wav
from agents.cosyvoice_client import CosyVoiceTTSClient
from server.app import create_app


def make_wav_bytes(seconds=0.1, rate=22050):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b"\x00\x00" * int(seconds * rate))
    return buf.getvalue()


def test_normalize_streaming_wav_header():
    data = bytearray(make_wav_bytes(0.2))
    struct.pack_into("<I", data, 4, 0x7FFFFF9B)
    struct.pack_into("<I", data, 40, 0x7FFFFF9B)
    fixed = CosyVoiceTTSClient._normalize_wav_header(bytes(data))
    with wave.open(io.BytesIO(fixed), "rb") as wf:
        assert wf.getnframes() == int(0.2 * 22050)
        assert wf.getframerate() == 22050


def test_build_tts_client_selects_cosyvoice(tmp_path):
    settings = make_settings(tmp_path, PBSC_TTS_PROVIDER="cosyvoice",
                             COSYVOICE_API_KEY="test-key", COSYVOICE_VOICE="longanyang")
    client = build_tts_client(settings)
    assert client.provider == "cosyvoice_ws" and client.voice == "longanyang"
    assert client.configured is True


def test_unconfigured_cosyvoice_writes_placeholder(tmp_path):
    client = CosyVoiceTTSClient("wss://example.invalid", None, "cosyvoice-v3-plus", "longanyang")
    out = tmp_path / "placeholder.wav"
    result = client.synthesize("你好", out)
    assert result.is_placeholder is True and out.is_file()
    assert result.duration == round(wav_duration(out), 3)


class FakeTTS:
    provider = "cosyvoice_ws"
    tts_model = "fake-tts"
    voice = "longanyang"
    configured = True

    def synthesize(self, text, out_path, voice="", lang="cn", trace_id="x"):
        duration = write_placeholder_wav(text, out_path)
        return TTSResult(out_path, duration, self.provider, False, 1, len(text), voice or self.voice)


def test_generate_audio_writes_page_narration_audio(tmp_path, sample_dir):
    settings = make_settings(tmp_path, PBSC_TTS_PROVIDER="cosyvoice", COSYVOICE_API_KEY="test-key")
    app = create_app(settings)
    try:
        pipeline = app.extensions["pbsc"]["pipeline"]
        pipeline.store.install_sample(sample_dir)
        pipeline.video.tts = FakeTTS()
        pipeline.tts = FakeTTS()
        client = app.test_client()
        submitted = client.post("/api/v1/generate/audio",
                                json={"book_id": "demo_book", "page_no": 1}).json["data"]
        assert submitted["kind"] == "audio"
        import time
        deadline = time.time() + 10
        while time.time() < deadline:
            task = client.get(f"/api/v1/tasks/{submitted['task_id']}").json["data"]
            if task["status"] in ("succeeded", "failed"):
                break
            time.sleep(0.05)
        assert task["status"] == "succeeded", task
        page = client.get("/api/v1/books/demo_book/pages/1").json["data"]
        audio = page["narration_audio"]
        assert audio["provider"] == "cosyvoice_ws" and audio["duration"] > 0
        assert client.get(f"/api/v1/media/demo_book/{audio['path']}").status_code == 200
    finally:
        app.extensions["pbsc"]["tasks"].shutdown(wait=True)
