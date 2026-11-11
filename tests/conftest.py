# -*- coding: utf-8 -*-
"""测试公共夹具。

外部服务（大模型、TTS、图生视频网关）用一个本机 HTTP 模拟服务代替，协议形状按参考代码实现。
它只能证明“我们的客户端按参考代码的协议收发是对的”，不能代替对真实服务的验证。
"""
from __future__ import annotations

import base64
import io
import json
import struct
import sys
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from configs.settings import load_settings  # noqa: E402

SMALL_VIDEO = {"PBSC_VIDEO_WIDTH": "320", "PBSC_VIDEO_HEIGHT": "180", "PBSC_VIDEO_FPS": "12",
               "PBSC_I2V_CLIP_SECONDS": "1", "PBSC_VIDEO_MIN_PAGE_SECONDS": "1.2",
               "PBSC_VIDEO_TAIL_PADDING": "0.4", "PBSC_VIDEO_CROSSFADE": "0.3",
               "PBSC_I2V_POLL_INTERVAL": "0.01", "PBSC_RETRY_BASE_DELAY": "0.01"}


def make_wav_bytes(seconds: float = 0.6, rate: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(struct.pack("<h", 0) * int(seconds * rate))
    return buf.getvalue()


def make_png_bytes(size=(320, 180), color=(180, 200, 240)) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def make_settings(tmp_path: Path, **env):
    base = {"PBSC_DATA_DIR": str(tmp_path / "data"), "PBSC_LOG_DIR": str(tmp_path / "logs"), **SMALL_VIDEO}
    base.update(env)
    settings = load_settings(base, use_dotenv=False)
    settings.ensure_dirs()
    return settings


@pytest.fixture
def settings(tmp_path):
    return make_settings(tmp_path)


@pytest.fixture
def app(settings):
    from server.app import create_app
    application = create_app(settings)
    yield application
    application.extensions["pbsc"]["tasks"].shutdown(wait=True)


@pytest.fixture
def client(app):
    return app.test_client()


class StubState:
    """模拟服务的可编程状态。"""

    def __init__(self):
        self.chat_script: list = []        # 依次返回：字符串=正文；整数=HTTP 状态码
        self.chat_requests: list = []
        self.tts_requests: list = []
        self.tts_status = 200
        self.i2v_submits: list = []
        self.i2v_headers: list = []
        self.poll_script: list = [2, 3]    # 每次查询依次返回的状态；用完后一直返回最后一个
        self.poll_count = 0
        self.submit_status = 200
        self.clip_bytes = b""
        self.base_url = ""


@pytest.fixture(scope="session")
def tiny_clip_bytes(tmp_path_factory) -> bytes:
    """一个 1 秒的小 mp4，充当图生视频服务“生成”的结果。"""
    from PIL import Image

    from agents.video_compose import render_kenburns
    work = tmp_path_factory.mktemp("clip")
    Image.new("RGB", (320, 180), (120, 160, 200)).save(work / "frame.png")
    render_kenburns(work / "frame.png", work / "clip.mp4", 1.0, (320, 180), 12, 0.05, (255, 255, 255))
    return (work / "clip.mp4").read_bytes()


@pytest.fixture
def stub(tiny_clip_bytes):
    state = StubState()
    state.clip_bytes = tiny_clip_bytes

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _json(self, status: int, body: dict) -> None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            if self.path == "/v1/chat/completions":
                state.chat_requests.append(body)
                item = state.chat_script.pop(0) if state.chat_script else "{}"
                if isinstance(item, int):
                    return self._json(item, {"error": {"message": "stub error"}})
                return self._json(200, {"model": body.get("model"),
                                        "choices": [{"message": {"role": "assistant", "content": item},
                                                     "finish_reason": "stop"}],
                                        "usage": {"prompt_tokens": 100, "completion_tokens": 40, "total_tokens": 140}})
            if self.path == "/tts":
                state.tts_requests.append(body)
                if state.tts_status != 200:
                    return self._json(state.tts_status, {"error": "stub"})
                return self._json(200, {"data": base64.b64encode(make_wav_bytes(0.8)).decode("ascii")})
            if self.path == "/v1/async/chat":
                state.i2v_submits.append(body)
                state.i2v_headers.append({k.lower(): v for k, v in self.headers.items()})
                if state.submit_status != 200:
                    return self._json(state.submit_status, {"error": "stub"})
                return self._json(200, {"task_id": f"stub-task-{len(state.i2v_submits)}"})
            self._json(404, {"error": "not found"})

        def do_GET(self):
            if self.path.startswith("/v1/async/results/"):
                index = min(state.poll_count, len(state.poll_script) - 1)
                status = state.poll_script[index]
                state.poll_count += 1
                if status == 3:
                    return self._json(200, {"status": 3, "response": {"video_url": f"{state.base_url}/files/clip.mp4"}})
                if status == 4:
                    return self._json(200, {"status": 4, "error": "内容审核未通过"})
                return self._json(200, {"status": status, "progress": 50})
            if self.path == "/files/clip.mp4":
                self.send_response(200)
                self.send_header("Content-Type", "video/mp4")
                self.send_header("Content-Length", str(len(state.clip_bytes)))
                self.end_headers()
                self.wfile.write(state.clip_bytes)
                return
            self._json(404, {"error": "not found"})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    state.base_url = f"http://127.0.0.1:{server.server_address[1]}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield state
    server.shutdown()
    server.server_close()


def service_env(stub_state, llm=True, tts=True, i2v=True) -> dict:
    env = {}
    if llm:
        env.update(SIMPLE_STORY_WRITER_URL=f"{stub_state.base_url}/v1/chat/completions",
                   SIMPLE_STORY_WRITER_API_KEY="test-key", SIMPLE_STORY_WRITER_MODEL="stub-llm")
    if tts:
        env.update(TTS_API_URL=f"{stub_state.base_url}/tts")
    if i2v:
        env.update(IMAGE2VIDEO_URL=stub_state.base_url, IMAGE2VIDEO_API_KEY="test-key",
                   IMAGE2VIDEO_MODEL="stub-i2v")
    return env


@pytest.fixture
def sample_dir() -> Path:
    return ROOT / "data" / "samples" / "demo_book"
