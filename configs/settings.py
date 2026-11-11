# -*- coding: utf-8 -*-
"""统一配置入口。

规则：
- 所有外部服务的地址和密钥只从环境变量读取（可放在仓库根目录的 .env 里，.env 不入库）。
- 外部服务的变量名与 参考代码/config.py 保持一致，一份 .env 可同时用于参考后端和本项目。
- 值为空或占位符 xxxx 视为“未配置”，对应功能自动走本地降级方案并在日志里标注。
- 本项目新增的变量统一以 PBSC_ 开头（Paper-Book-Smart-Companion）。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Mapping, Optional

ROOT = Path(__file__).resolve().parent.parent
PLACEHOLDERS = {"", "xxxx", "your-key-here", "changeme"}
SECRET_HINTS = ("key", "token", "secret", "password", "authorization")


def load_dotenv(path: Optional[Path] = None, environ: Optional[dict] = None) -> list[str]:
    """读取 .env（KEY=VALUE，每行一项）。已存在的环境变量不会被覆盖。返回本次载入的变量名。"""
    path = path or ROOT / ".env"
    environ = os.environ if environ is None else environ
    loaded: list[str] = []
    if not path.is_file():
        return loaded
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and key not in environ:
            environ[key] = value
            loaded.append(key)
    return loaded


class _Env:
    def __init__(self, environ: Mapping[str, str]):
        self.environ = environ

    def text(self, name: str, default: Optional[str] = None) -> Optional[str]:
        value = self.environ.get(name)
        if value is None or value.strip() in PLACEHOLDERS:
            return default
        return value.strip()

    def integer(self, name: str, default: int) -> int:
        value = self.text(name)
        try:
            return int(value) if value is not None else default
        except ValueError as exc:
            raise ValueError(f"环境变量 {name} 应为整数，当前为 {value!r}") from exc

    def number(self, name: str, default: float) -> float:
        value = self.text(name)
        try:
            return float(value) if value is not None else default
        except ValueError as exc:
            raise ValueError(f"环境变量 {name} 应为数字，当前为 {value!r}") from exc

    def flag(self, name: str, default: bool = False) -> bool:
        value = self.text(name)
        if value is None:
            return default
        return value.lower() in ("1", "true", "yes", "on")

    def path(self, name: str, default: Path) -> Path:
        value = self.text(name)
        p = Path(value) if value else default
        return p if p.is_absolute() else (ROOT / p)


@dataclass(frozen=True)
class ServiceConfig:
    """一个外部服务的连接信息。"""
    name: str
    url: Optional[str] = None
    api_key: Optional[str] = None
    model: Optional[str] = None
    extra: dict = field(default_factory=dict)

    @property
    def configured(self) -> bool:
        return bool(self.url)

    def public(self) -> dict:
        return {"configured": self.configured, "model": self.model,
                "url": mask_url(self.url), "api_key": "已配置" if self.api_key else "未配置"}


@dataclass(frozen=True)
class VideoConfig:
    width: int = 1280
    height: int = 720
    fps: int = 24
    clip_seconds: int = 5              # 图生视频单段时长
    resolution: str = "720p"
    aspect_ratio: str = "16:9"
    crossfade: float = 0.5             # 页间转场时长（秒）
    tail_padding: float = 0.8          # 旁白结束后画面再停留的时间
    min_page_seconds: float = 3.0
    max_slowdown: float = 1.25         # 片段不够长时最多放慢的倍数，其余用末帧定格补足
    subtitle_font: Optional[str] = None
    subtitle_max_chars: int = 20
    background: tuple = (247, 243, 232)
    i2v_image_mode: str = "base64"     # base64：直接传图；url：传公网地址（需 PBSC_PUBLIC_BASE_URL）
    i2v_max_image_side: int = 1280
    i2v_poll_interval: float = 5.0
    i2v_max_wait: float = 300.0
    i2v_priority: str = "5"


@dataclass(frozen=True)
class Settings:
    root: Path
    data_dir: Path
    books_dir: Path
    samples_dir: Path
    log_dir: Path
    prompts_dir: Path
    pricing_file: Path
    host: str
    port: int
    debug: bool
    cors_origins: str
    public_base_url: Optional[str]
    max_upload_mb: float
    max_image_pixels: int
    max_pages_per_upload: int
    task_workers: int
    retry_attempts: int
    retry_base_delay: float
    request_timeout: float
    narration_max_chars: int
    narration_temperature: float
    narration_attempts: int
    tts_voice: str
    llm: ServiceConfig
    tts: ServiceConfig
    i2v: ServiceConfig
    video: VideoConfig

    @property
    def tasks_dir(self) -> Path:
        return self.log_dir / "tasks"

    @property
    def runs_dir(self) -> Path:
        return self.log_dir / "runs"

    @property
    def ledger_file(self) -> Path:
        return self.log_dir / "cost_ledger.jsonl"

    def ensure_dirs(self) -> None:
        for p in (self.data_dir, self.books_dir, self.log_dir, self.runs_dir, self.tasks_dir):
            p.mkdir(parents=True, exist_ok=True)

    def public_summary(self) -> dict:
        """可以放进接口返回和日志的配置摘要（不含任何密钥）。"""
        return {
            "services": {s.name: s.public() for s in (self.llm, self.tts, self.i2v)},
            "limits": {"max_upload_mb": self.max_upload_mb, "max_pages_per_upload": self.max_pages_per_upload,
                       "task_workers": self.task_workers},
            "video": {"size": [self.video.width, self.video.height], "fps": self.video.fps,
                      "clip_seconds": self.video.clip_seconds, "crossfade": self.video.crossfade},
        }


def mask_url(url: Optional[str]) -> Optional[str]:
    """只保留协议和主机，隐藏路径和查询串里可能出现的凭据。"""
    if not url:
        return None
    from urllib.parse import urlsplit
    parts = urlsplit(url)
    if not parts.scheme:
        return "***"
    host = parts.hostname or ""
    port = f":{parts.port}" if parts.port else ""
    return f"{parts.scheme}://{host}{port}/…"


def redact(value, _key: str = ""):
    """递归遮蔽字典里疑似密钥的字段，写日志前调用。"""
    if isinstance(value, dict):
        return {k: redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v, _key) for v in value]
    if isinstance(value, str) and _key and any(h in _key.lower() for h in SECRET_HINTS):
        return "***"
    return value


def load_settings(environ: Optional[Mapping[str, str]] = None, use_dotenv: bool = True) -> Settings:
    if environ is None:
        if use_dotenv:
            load_dotenv()
        environ = os.environ
    env = _Env(environ)

    data_dir = env.path("PBSC_DATA_DIR", ROOT / "data")
    log_dir = env.path("PBSC_LOG_DIR", ROOT / "logs")
    bg = env.text("PBSC_VIDEO_BACKGROUND", "247,243,232")
    try:
        background = tuple(int(x) for x in bg.split(","))
        assert len(background) == 3
    except Exception as exc:
        raise ValueError("PBSC_VIDEO_BACKGROUND 应为 'R,G,B'") from exc

    video = VideoConfig(
        width=env.integer("PBSC_VIDEO_WIDTH", 1280),
        height=env.integer("PBSC_VIDEO_HEIGHT", 720),
        fps=env.integer("PBSC_VIDEO_FPS", 24),
        clip_seconds=env.integer("PBSC_I2V_CLIP_SECONDS", 5),
        resolution=env.text("PBSC_I2V_RESOLUTION", "720p"),
        aspect_ratio=env.text("PBSC_I2V_ASPECT_RATIO", "16:9"),
        crossfade=env.number("PBSC_VIDEO_CROSSFADE", 0.5),
        tail_padding=env.number("PBSC_VIDEO_TAIL_PADDING", 0.8),
        min_page_seconds=env.number("PBSC_VIDEO_MIN_PAGE_SECONDS", 3.0),
        max_slowdown=env.number("PBSC_VIDEO_MAX_SLOWDOWN", 1.25),
        subtitle_font=env.text("PBSC_SUBTITLE_FONT"),
        subtitle_max_chars=env.integer("PBSC_SUBTITLE_MAX_CHARS", 20),
        background=background,
        i2v_image_mode=env.text("PBSC_I2V_IMAGE_MODE", "base64"),
        i2v_max_image_side=env.integer("PBSC_I2V_MAX_IMAGE_SIDE", 1280),
        i2v_poll_interval=env.number("PBSC_I2V_POLL_INTERVAL", 5.0),
        i2v_max_wait=env.number("PBSC_I2V_MAX_WAIT", 300.0),
        i2v_priority=env.text("PBSC_I2V_PRIORITY", "5"),
    )
    if video.i2v_image_mode not in ("base64", "url"):
        raise ValueError("PBSC_I2V_IMAGE_MODE 只能是 base64 或 url")

    return Settings(
        root=ROOT,
        data_dir=data_dir,
        books_dir=data_dir / "books",
        samples_dir=ROOT / "data" / "samples",
        log_dir=log_dir,
        prompts_dir=ROOT / "configs" / "prompts",
        pricing_file=env.path("PBSC_PRICING_FILE", ROOT / "configs" / "pricing.json"),
        host=env.text("PBSC_HOST", "127.0.0.1"),
        port=env.integer("PBSC_PORT", 8000),
        debug=env.flag("PBSC_DEBUG", False),
        cors_origins=env.text("PBSC_CORS_ORIGINS", "*"),
        public_base_url=env.text("PBSC_PUBLIC_BASE_URL"),
        max_upload_mb=env.number("PBSC_MAX_UPLOAD_MB", 20.0),
        max_image_pixels=env.integer("PBSC_MAX_IMAGE_PIXELS", 40_000_000),
        max_pages_per_upload=env.integer("PBSC_MAX_PAGES_PER_UPLOAD", 60),
        task_workers=env.integer("PBSC_TASK_WORKERS", 2),
        retry_attempts=env.integer("PBSC_RETRY_ATTEMPTS", 3),
        retry_base_delay=env.number("PBSC_RETRY_BASE_DELAY", 1.0),
        request_timeout=env.number("PBSC_REQUEST_TIMEOUT", 120.0),
        narration_max_chars=env.integer("PBSC_NARRATION_MAX_CHARS", 220),
        narration_temperature=env.number("PBSC_NARRATION_TEMPERATURE", 0.3),
        narration_attempts=env.integer("PBSC_NARRATION_ATTEMPTS", 3),
        tts_voice=env.text("PBSC_TTS_VOICE", "xiaosi"),
        llm=ServiceConfig(
            name="llm",
            url=env.text("SIMPLE_STORY_WRITER_URL"),
            api_key=env.text("SIMPLE_STORY_WRITER_API_KEY"),
            model=env.text("SIMPLE_STORY_WRITER_MODEL", "deepseek-v3.2"),
        ),
        tts=ServiceConfig(name="tts", url=env.text("TTS_API_URL"), model=env.text("PBSC_TTS_MODEL", "v2_llm")),
        i2v=ServiceConfig(
            name="i2v",
            url=env.text("IMAGE2VIDEO_URL"),
            api_key=env.text("IMAGE2VIDEO_API_KEY"),
            model=env.text("IMAGE2VIDEO_MODEL", "doubao-seedance-1.0-lite-i2v"),
        ),
        video=video,
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = load_settings()
    settings.ensure_dirs()
    return settings


def reset_settings() -> None:
    """测试或修改环境变量后调用，下次 get_settings() 会重新读取。"""
    get_settings.cache_clear()
