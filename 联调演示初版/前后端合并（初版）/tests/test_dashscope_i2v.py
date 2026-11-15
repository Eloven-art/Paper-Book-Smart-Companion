# -*- coding: utf-8 -*-
"""阿里云百炼图生视频适配层测试。

用假 DashScope client 验证请求参数、media/image_url 两种首帧协议、失败处理和
VideoAgent/健康检查的 provider 选择；不发起真实付费调用。
"""
from pathlib import Path

import pytest
from PIL import Image

from conftest import make_settings
from agents.i2v_client import DashScopeI2V
from agents.pipeline import build_pipeline
from server.app import create_app
from server.errors import ExternalServiceError, ServiceNotConfigured


class FakeJob:
    def __init__(self, succeeded=True, video_url="https://example.invalid/video.mp4",
                 task_id="task-1", message=None, output=None):
        self.succeeded = succeeded
        self.video_url = video_url
        self.task_id = task_id
        self.message = message
        self.output = output or {}


class FakeVideos:
    def __init__(self, job):
        self.job = job
        self.calls = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        return self.job


class FakeClient:
    def __init__(self, job):
        self.videos = FakeVideos(job)


def page_image(tmp_path: Path) -> Path:
    path = tmp_path / "page.png"
    Image.new("RGB", (320, 180), (200, 220, 240)).save(path)
    return path


def test_dashscope_wan3_uses_media_protocol(tmp_path, tiny_clip_bytes, monkeypatch):
    image, out = page_image(tmp_path), tmp_path / "clip.mp4"
    client = FakeClient(FakeJob())
    engine = DashScopeI2V(api_key="test-key", model="wan3.0-video", client=client)
    monkeypatch.setattr(engine, "_download", lambda url, path: Path(path).write_bytes(tiny_clip_bytes))

    result = engine.generate(image, "一只猫在草地上奔跑", out, 5)

    call = client.videos.calls[0]
    assert call["media"] == [{"type": "first_frame", "url": str(image.resolve())}]
    assert "image_url" not in call
    assert call["duration"] == 5 and call["resolution"] == "1080P" and call["model"] == "wan3.0-video"
    assert result.provider == "dashscope_video" and result.model == "wan3.0-video"
    assert result.task_id == "task-1" and result.is_fallback is False and result.billable is True


def test_dashscope_wan2_uses_image_url_protocol(tmp_path, tiny_clip_bytes, monkeypatch):
    image, out = page_image(tmp_path), tmp_path / "clip.mp4"
    client = FakeClient(FakeJob(task_id="task-2"))
    engine = DashScopeI2V(api_key="test-key", model="wan2.1-i2v-turbo", client=client)
    monkeypatch.setattr(engine, "_download", lambda url, path: Path(path).write_bytes(tiny_clip_bytes))

    engine.generate(image, "prompt", out, 5)

    call = client.videos.calls[0]
    assert call["image_url"] == str(image.resolve()) and "media" not in call


def test_dashscope_failure_is_reported(tmp_path):
    image, out = page_image(tmp_path), tmp_path / "clip.mp4"
    job = FakeJob(succeeded=False, video_url=None, message="内容审核未通过")
    engine = DashScopeI2V(api_key="test-key", model="wan3.0-video", client=FakeClient(job))
    with pytest.raises(ExternalServiceError) as exc:
        engine.generate(image, "prompt", out, 5)
    assert exc.value.code == "EXTERNAL_SERVICE_ERROR"
    assert "审核" in exc.value.message


def test_dashscope_requires_key():
    engine = DashScopeI2V(api_key=None, model="wan3.0-video")
    assert engine.configured is False
    with pytest.raises(ServiceNotConfigured):
        engine.generate(Path("missing.png"), "prompt", Path("out.mp4"), 5)


def test_video_agent_selects_dashscope_provider(tmp_path):
    settings = make_settings(
        tmp_path, PBSC_I2V_PROVIDER="dashscope", DASHSCOPE_API_KEY="test-key",
        PBSC_DASHSCOPE_MODEL="wan3.0-video-prime", PBSC_DASHSCOPE_RESOLUTION="1080P",
        PBSC_DASHSCOPE_BASE_URL="https://ws-x.cn-beijing.maas.aliyuncs.com/api/v1",
        PBSC_VIDEO_WIDTH="1920", PBSC_VIDEO_HEIGHT="1080")
    pipe = build_pipeline(settings)
    assert pipe.settings.i2v_provider == "dashscope"
    assert pipe.video.i2v.provider == "dashscope_video"
    assert pipe.video.i2v.model == "wan3.0-video-prime"
    assert pipe.video.i2v.resolution == "1080P"
    assert pipe.video.i2v.base_url.endswith("/api/v1")
    assert pipe.settings.video.width == 1920 and pipe.settings.video.height == 1080
    assert pipe.video.i2v.configured is True


def test_health_reports_dashscope_without_leaking_key(tmp_path):
    settings = make_settings(tmp_path, PBSC_I2V_PROVIDER="dashscope", DASHSCOPE_API_KEY="test-key")
    app = create_app(settings)
    try:
        data = app.test_client().get("/api/v1/health").json["data"]
        assert data["i2v_provider"] == "dashscope"
        assert data["dashscope"]["configured"] is True
        assert data["dashscope"]["resolution"] == "1080P"
        assert data["dashscope"]["api_key"] == "已配置"
        assert data["services"]["i2v"]["configured"] is True
        assert "test-key" not in str(data)
    finally:
        app.extensions["pbsc"]["tasks"].shutdown(wait=True)
