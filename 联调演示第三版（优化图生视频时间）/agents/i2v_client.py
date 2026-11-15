# -*- coding: utf-8 -*-
"""图生视频：以原绘本页插图为首帧生成短视频片段。

两种实现，接口相同（generate(image_path, prompt, out_path, seconds) -> I2VResult）：

1. GatewayI2V —— 真实服务。协议沿用参考代码（参考代码/generate_video.py 的 VideoGenerator）：
     提交  POST {IMAGE2VIDEO_URL}/v1/async/chat          请求头 api-key / X-APX-Model / x-apx-task-priority
     查询  GET  {IMAGE2VIDEO_URL}/v1/async/results/<id>  status: 2 进行中 / 3 完成 / 4 失败
     完成后从 response.video_url 下载 mp4
   首帧默认用 base64 直接放进请求体（不依赖图床）；设 PBSC_I2V_IMAGE_MODE=url 且配置
   PBSC_PUBLIC_BASE_URL 时改传公网地址。
   注意：本机没有真实网关的地址和密钥，这部分只用本地模拟服务验证过协议流程，未做真实调用。

2. KenBurnsI2V —— 本地降级方案。把静态页图做成缓慢推近的片段，不调用任何外部服务、不产生费用。
   画面只有镜头运动，没有 AI 生成的动作；结果里标 is_fallback=true，演示和说明书里要如实说明。
"""
from __future__ import annotations

import base64
import io
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import requests

from server.errors import ExternalServiceError, ServiceNotConfigured

KENBURNS_ZOOM = 0.06       # 降级片段从 1.00 倍匀速推近到 1.06 倍


@dataclass
class I2VResult:
    path: Path
    seconds: float
    provider: str
    model: Optional[str]
    is_fallback: bool
    elapsed_ms: int
    task_id: Optional[str] = None
    billable: bool = False
    details: dict = field(default_factory=dict)


def encode_first_frame(image_path: Path, max_side: int = 1280) -> tuple[str, str, tuple[int, int]]:
    """把页图缩到合适尺寸并转成 base64。返回 (base64, mime, (宽, 高))。"""
    from PIL import Image
    with Image.open(image_path) as img:
        img = img.convert("RGB")
        scale = min(1.0, max_side / max(img.size))
        if scale < 1.0:
            img = img.resize((int(img.width * scale), int(img.height * scale)), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=92)
        return base64.b64encode(buf.getvalue()).decode("ascii"), "image/jpeg", img.size


class KenBurnsI2V:
    provider = "local_kenburns"
    model = None
    configured = True

    def __init__(self, size: tuple[int, int] = (1280, 720), fps: int = 24, background: tuple = (247, 243, 232)):
        self.size = size
        self.fps = fps
        self.background = background

    def generate(self, image_path: Path, prompt: str, out_path: Path, seconds: float = 5.0,
                 on_progress: Optional[Callable[[str], None]] = None) -> I2VResult:
        from .video_compose import render_kenburns
        started = time.perf_counter()
        render_kenburns(Path(image_path), Path(out_path), seconds, self.size, self.fps, KENBURNS_ZOOM, self.background)
        return I2VResult(Path(out_path), float(seconds), self.provider, None, True,
                         int((time.perf_counter() - started) * 1000),
                         details={"zoom": KENBURNS_ZOOM, "note": "本地降级：静态页图缓慢推近，非 AI 生成视频"})


class GatewayI2V:
    provider = "reference_i2v_gateway"

    def __init__(self, base_url: Optional[str], api_key: Optional[str], model: Optional[str], *,
                 priority: str = "5", resolution: str = "720p", aspect_ratio: str = "16:9",
                 image_mode: str = "base64", max_image_side: int = 1280, poll_interval: float = 5.0,
                 max_wait: float = 300.0, public_url_for: Optional[Callable[[Path], Optional[str]]] = None,
                 session: Optional[requests.Session] = None, sleep: Callable[[float], None] = time.sleep):
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key or ""
        self.model = model
        self.priority = priority
        self.resolution = resolution
        self.aspect_ratio = aspect_ratio
        self.image_mode = image_mode
        self.max_image_side = max_image_side
        self.poll_interval = poll_interval
        self.max_wait = max_wait
        self.public_url_for = public_url_for
        self._session = session or requests.Session()
        self._sleep = sleep

    @property
    def configured(self) -> bool:
        return bool(self.base_url)

    def _headers(self) -> dict:
        return {"api-key": self.api_key, "X-APX-Model": self.model or "", "x-apx-task-priority": self.priority,
                "Content-Type": "application/json; charset=utf-8"}

    def _request(self, method: str, url: str, **kwargs) -> requests.Response:
        try:
            resp = self._session.request(method, url, **kwargs)
        except requests.Timeout as exc:
            raise ExternalServiceError("图生视频服务请求超时", service="i2v", retryable=True) from exc
        except requests.RequestException as exc:
            raise ExternalServiceError(f"图生视频服务请求失败：{type(exc).__name__}", service="i2v",
                                       retryable=isinstance(exc, requests.ConnectionError)) from exc
        if resp.status_code >= 400:
            raise ExternalServiceError(f"图生视频服务返回 HTTP {resp.status_code}", service="i2v",
                                       status=resp.status_code,
                                       retryable=resp.status_code in (408, 429) or resp.status_code >= 500,
                                       details={"body": resp.text[:300]})
        return resp

    def build_payload(self, image_path: Path, prompt: str, seconds: float) -> dict:
        """请求体格式与参考代码一致：url 模式带时长/分辨率/宽高比字段，base64 模式用 content 列表。"""
        if self.image_mode == "url":
            url = self.public_url_for(image_path) if self.public_url_for else None
            if not url:
                raise ServiceNotConfigured("url 传图模式需要配置 PBSC_PUBLIC_BASE_URL", details={"service": "i2v"})
            return {"model": self.model, "prompt": prompt, "duration": int(round(seconds)),
                    "resolution": self.resolution, "aspect_ratio": self.aspect_ratio, "img_url": url,
                    "extra_body": None}
        encoded, mime, _ = encode_first_frame(image_path, self.max_image_side)
        return {"model": self.model,
                "content": [{"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}}]}

    def submit(self, image_path: Path, prompt: str, seconds: float) -> str:
        payload = self.build_payload(image_path, prompt, seconds)
        resp = self._request("POST", f"{self.base_url}/v1/async/chat", headers=self._headers(), json=payload,
                             timeout=(30, 180))
        try:
            body = resp.json()
        except ValueError as exc:
            raise ExternalServiceError("图生视频提交接口返回的不是 JSON", service="i2v") from exc
        task_id = body.get("task_id") or body.get("id") or body.get("request_id")
        if not task_id:
            raise ExternalServiceError("图生视频提交成功但没有返回任务号", service="i2v",
                                       details={"body": str(body)[:300]})
        return str(task_id)

    def poll(self, task_id: str, on_progress: Optional[Callable[[str], None]] = None) -> str:
        """轮询到完成，返回视频下载地址。"""
        deadline = time.monotonic() + self.max_wait
        while True:
            resp = self._request("GET", f"{self.base_url}/v1/async/results/{task_id}", headers=self._headers(),
                                 timeout=(5, 30))
            try:
                body = resp.json()
            except ValueError as exc:
                raise ExternalServiceError("图生视频查询接口返回的不是 JSON", service="i2v") from exc
            status = body.get("status")
            if status == 3:
                url = (body.get("response") or {}).get("video_url") if isinstance(body.get("response"), dict) else None
                if not url:
                    raise ExternalServiceError("图生视频任务完成但没有视频地址", service="i2v",
                                               details={"body": str(body)[:300]})
                return url
            if status == 4:
                raise ExternalServiceError(f"图生视频任务失败：{str(body.get('error', '未知原因'))[:200]}",
                                           service="i2v", details={"task_id": task_id})
            if on_progress:
                on_progress(f"图生视频生成中（进度 {body.get('progress', '未知')}）")
            if time.monotonic() >= deadline:
                raise ExternalServiceError(f"图生视频任务超过 {self.max_wait:.0f} 秒仍未完成", service="i2v",
                                           details={"task_id": task_id})
            self._sleep(self.poll_interval)

    def download(self, url: str, out_path: Path) -> None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = out_path.with_suffix(".part")
        try:
            with self._session.get(url, headers={"api-key": self.api_key}, stream=True, timeout=(10, 120)) as resp:
                if resp.status_code >= 400:
                    raise ExternalServiceError(f"下载视频返回 HTTP {resp.status_code}", service="i2v",
                                               status=resp.status_code, retryable=resp.status_code >= 500)
                with open(tmp, "wb") as fh:
                    for chunk in resp.iter_content(chunk_size=1 << 16):
                        if chunk:
                            fh.write(chunk)
        except requests.RequestException as exc:
            tmp.unlink(missing_ok=True)
            raise ExternalServiceError(f"下载视频失败：{type(exc).__name__}", service="i2v", retryable=True) from exc
        if tmp.stat().st_size < 1024:
            tmp.unlink(missing_ok=True)
            raise ExternalServiceError("下载到的视频文件异常（过小）", service="i2v", retryable=True)
        tmp.replace(out_path)

    def generate(self, image_path: Path, prompt: str, out_path: Path, seconds: float = 5.0,
                 on_progress: Optional[Callable[[str], None]] = None) -> I2VResult:
        if not self.configured:
            raise ServiceNotConfigured("图生视频服务未配置（IMAGE2VIDEO_URL）", details={"service": "i2v"})
        from .video_compose import probe_duration
        started = time.perf_counter()
        task_id = self.submit(Path(image_path), prompt, seconds)
        if on_progress:
            on_progress("图生视频任务已提交")
        video_url = self.poll(task_id, on_progress)
        self.download(video_url, Path(out_path))
        return I2VResult(Path(out_path), probe_duration(Path(out_path)), self.provider, self.model, False,
                         int((time.perf_counter() - started) * 1000), task_id=task_id, billable=True,
                         details={"image_mode": self.image_mode, "resolution": self.resolution,
                                  "aspect_ratio": self.aspect_ratio, "requested_seconds": seconds})


def dashscope_seconds(seconds: float) -> int:
    return max(5, min(10, int(round(float(seconds)))))


class DashScopeI2V:
    """阿里云百炼（DashScope）图生视频。

    使用官方 `dashscope>=2.1` SDK：把页图作为 first_frame 直接传给
    `client.videos.generate(...)`。SDK 会把本地图片先上传到 DashScope 的临时 OSS，
    因此不要求本机有公网地址。

    密钥只从环境变量 / `.env` 读取（`DASHSCOPE_API_KEY`），不写进代码、不下发前端、
    不写进运行记录。未配置时由 VideoAgent 自动走本地降级片段。
    """
    provider = "dashscope_video"

    def __init__(self, api_key: Optional[str], model: str = "wan3.0-video", *,
                 workspace: Optional[str] = None, region: str = "cn-beijing", base_url: Optional[str] = None,
                 media_protocol: str = "auto", resolution: str = "1080P", ratio: str = "adaptive",
                 prompt_extend: bool = True, watermark: bool = False, timeout: float = 300.0,
                 poll_interval: float = 5.0, max_interval: float = 15.0,
                 client=None, session: Optional[requests.Session] = None):
        self.api_key = api_key or ""
        self.model = model
        self.workspace = workspace or None
        self.region = region or "cn-beijing"
        self.base_url = base_url or None
        self.media_protocol = media_protocol or "auto"
        self.resolution = resolution or "720P"
        self.ratio = ratio or "adaptive"
        self.prompt_extend = bool(prompt_extend)
        self.watermark = bool(watermark)
        self.timeout = float(timeout)
        self.poll_interval = float(poll_interval)
        self.max_interval = float(max_interval)
        self._client = client
        self._session = session or requests.Session()

    @property
    def configured(self) -> bool:
        return bool(self.api_key or self._client is not None)

    def _get_client(self):
        if self._client is not None:
            return self._client
        if not self.api_key:
            raise ServiceNotConfigured(
                "阿里云百炼图生视频未配置（DASHSCOPE_API_KEY）",
                details={"service": "i2v", "provider": "dashscope"})
        try:
            import dashscope
        except ImportError as exc:
            raise ServiceNotConfigured(
                "未安装 dashscope SDK，请执行 pip install -r requirements.txt",
                details={"service": "i2v", "provider": "dashscope"}) from exc
        try:
            self._client = dashscope.Client(api_key=self.api_key, workspace=self.workspace,
                                            region=self.region, base_url=self.base_url,
                                            timeout=self.timeout)
        except Exception as exc:       # noqa: BLE001
            raise ExternalServiceError(f"阿里云百炼客户端初始化失败：{exc}", service="i2v",
                                       details={"provider": "dashscope"}) from exc
        return self._client

    def _uses_media_protocol(self) -> bool:
        if self.media_protocol == "media":
            return True
        if self.media_protocol == "image_url":
            return False
        # 新版 wan3 / wan2.7-i2v 使用 media；旧版 wan2.x 用 img_url。
        return self.model.startswith(("wan3", "wan2.7-i2v"))

    def _download(self, url: str, out_path: Path) -> None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = out_path.with_suffix(".part")
        try:
            with self._session.get(url, stream=True, timeout=(10, 180)) as response:
                if response.status_code >= 400:
                    raise ExternalServiceError(f"下载百炼视频返回 HTTP {response.status_code}", service="i2v",
                                               status=response.status_code,
                                               retryable=response.status_code >= 500)
                with open(tmp, "wb") as fh:
                    for chunk in response.iter_content(chunk_size=1 << 16):
                        if chunk:
                            fh.write(chunk)
        except requests.RequestException as exc:
            tmp.unlink(missing_ok=True)
            raise ExternalServiceError(f"下载百炼视频失败：{type(exc).__name__}", service="i2v",
                                       retryable=True) from exc
        if tmp.stat().st_size < 1024:
            tmp.unlink(missing_ok=True)
            raise ExternalServiceError("下载到的百炼视频文件异常（过小）", service="i2v", retryable=True)
        tmp.replace(out_path)

    def generate(self, image_path: Path, prompt: str, out_path: Path, seconds: float = 5.0,
                 on_progress: Optional[Callable[[str], None]] = None) -> I2VResult:
        if not self.configured:
            raise ServiceNotConfigured("阿里云百炼图生视频未配置（DASHSCOPE_API_KEY）",
                                       details={"service": "i2v", "provider": "dashscope"})
        from .video_compose import probe_duration
        started = time.perf_counter()
        # DashScope 的视频时长以秒为单位，一般支持 5/10 秒；这里夹到 5–10。
        duration = dashscope_seconds(seconds)
        kwargs = {
            "model": self.model,
            "prompt": prompt,
            "duration": duration,
            "resolution": str(self.resolution).upper(),
            "ratio": self.ratio,
            "prompt_extend": self.prompt_extend,
            "watermark": self.watermark,
            "timeout": self.timeout,
            "poll_interval": self.poll_interval,
            "max_interval": self.max_interval,
        }
        if self._uses_media_protocol():
            kwargs["media"] = [{"type": "first_frame", "url": str(Path(image_path).resolve())}]
        else:
            kwargs["image_url"] = str(Path(image_path).resolve())
        try:
            client = self._get_client()
            if on_progress:
                on_progress("阿里云百炼视频任务已提交，等待生成")
            job = client.videos.generate(**kwargs)
        except Exception as exc:       # noqa: BLE001 - SDK 的异常层次随版本变化
            status = getattr(exc, "status_code", None)
            retryable = status in (408, 429) or (isinstance(status, int) and status >= 500)
            raise ExternalServiceError(
                f"阿里云百炼图生视频失败：{exc}", service="i2v", status=status, retryable=retryable,
                details={"provider": "dashscope", "model": self.model}) from exc
        if not getattr(job, "succeeded", False) or not getattr(job, "video_url", None):
            message = getattr(job, "message", None) or (getattr(job, "output", None) or {})
            raise ExternalServiceError(
                f"阿里云百炼图生视频任务失败：{str(message)[:300]}", service="i2v",
                details={"provider": "dashscope", "model": self.model,
                         "task_id": getattr(job, "task_id", None)})
        task_id = getattr(job, "task_id", None)
        video_url = job.video_url
        job_output = getattr(job, "output", None) or {}
        timing = {key: job_output.get(key) for key in
                  ("task_id", "task_status", "submit_time", "scheduled_time", "end_time")
                  if key in job_output}
        self._download(video_url, Path(out_path))
        return I2VResult(
            Path(out_path), probe_duration(Path(out_path)), self.provider, self.model, False,
            int((time.perf_counter() - started) * 1000), task_id=task_id, billable=True,
            details={"provider": "dashscope", "media_protocol": "media" if "media" in kwargs else "image_url",
                     "resolution": kwargs["resolution"], "ratio": self.ratio,
                     "prompt_extend": self.prompt_extend, "watermark": self.watermark,
                     "requested_seconds": seconds, "requested_duration": duration,
                     "job_timing": timing})
