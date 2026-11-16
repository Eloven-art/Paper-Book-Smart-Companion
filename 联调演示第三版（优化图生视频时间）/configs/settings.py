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
    subtitles_enabled: bool = False    # 默认不把旁白字幕烧进画面，避免遮挡原书文字
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
    web_dir: Path
    install_sample: bool
    voice_service_url: Optional[str]
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
    pdf_max_mb: int
    pdf_max_pages: int
    pdf_max_edge: int
    pdf_max_pixels: int
    task_workers: int
    video_workers: int
    retry_attempts: int
    retry_base_delay: float
    request_timeout: float
    narration_max_chars: int
    narration_temperature: float
    narration_attempts: int
    tts_voice: str
    ocr_engine: str
    ocr_model_dir: Path
    ocr_text_score: float
    ocr_use_cls: bool
    ocr_auto: bool
    asr_model_dir: Path
    asr_language: str
    asr_use_itn: bool
    asr_intra_threads: int
    asr_max_seconds: float
    vlm_url: Optional[str]
    vlm_api_key: Optional[str]
    vlm_model: Optional[str]
    vlm_protocol: str
    vlm_bbox_format: str
    vlm_timeout: float
    vlm_max_image_side: int
    vlm_temperature: float
    vlm_auto: bool
    auto_generate: bool
    tts_provider: str
    cosyvoice_ws_url: str
    cosyvoice_api_key: Optional[str]
    cosyvoice_workspace: Optional[str]
    cosyvoice_region: str
    cosyvoice_model: str
    cosyvoice_voice: str
    cosyvoice_format: str
    cosyvoice_sample_rate: int
    cosyvoice_volume: int
    cosyvoice_rate: float
    cosyvoice_pitch: float
    cosyvoice_timeout: float
    cosyvoice_language_hints: tuple
    cosyvoice_instruction: Optional[str]
    cosyvoice_enable_ssml: bool
    i2v_provider: str
    dashscope_api_key: Optional[str]
    dashscope_workspace: Optional[str]
    dashscope_region: str
    dashscope_base_url: Optional[str]
    dashscope_model: str
    dashscope_resolution: str
    dashscope_media_protocol: str
    dashscope_ratio: str
    dashscope_prompt_extend: bool
    dashscope_watermark: bool
    dashscope_timeout: float
    dashscope_poll_interval: float
    dashscope_max_interval: float
    i2v_max_pages: int
    llm: ServiceConfig
    tts: ServiceConfig
    i2v: ServiceConfig
    video: VideoConfig
    qa_top_k: int = 4
    qa_max_chars: int = 160
    qa_temperature: float = 0.2
    qa_tts: bool = True

    @property
    def tasks_dir(self) -> Path:
        return self.log_dir / "tasks"

    @property
    def runs_dir(self) -> Path:
        return self.log_dir / "runs"

    @property
    def tts_dir(self) -> Path:
        return self.data_dir / "tts"

    @property
    def history_dir(self) -> Path:
        return self.data_dir / "history"

    @property
    def qa_dir(self) -> Path:
        return self.data_dir / "qa"

    @property
    def voice_selection_file(self) -> Path:
        return self.data_dir / "voice_selection.json"

    @property
    def ledger_file(self) -> Path:
        return self.log_dir / "cost_ledger.jsonl"

    def ensure_dirs(self) -> None:
        for p in (self.data_dir, self.books_dir, self.log_dir, self.runs_dir, self.tasks_dir, self.tts_dir,
                  self.history_dir, self.qa_dir):
            p.mkdir(parents=True, exist_ok=True)

    def public_summary(self) -> dict:
        """可以放进接口返回和日志的配置摘要（不含任何密钥）。"""
        services = {s.name: s.public() for s in (self.llm, self.tts, self.i2v)}
        if self.tts_provider == "cosyvoice":
            services["tts"] = {"configured": bool(self.cosyvoice_api_key), "model": self.cosyvoice_model,
                               "url": "cosyvoice-websocket", "api_key": "已配置" if self.cosyvoice_api_key else "未配置"}
        if self.i2v_provider == "dashscope":
            services["i2v"] = {"configured": bool(self.dashscope_api_key), "model": self.dashscope_model,
                               "url": "dashscope", "api_key": "已配置" if self.dashscope_api_key else "未配置"}
        return {
            "services": services,
            "limits": {"max_upload_mb": self.max_upload_mb, "max_pages_per_upload": self.max_pages_per_upload,
                       "task_workers": self.task_workers},
            "video": {"size": [self.video.width, self.video.height], "fps": self.video.fps,
                      "clip_seconds": self.video.clip_seconds, "crossfade": self.video.crossfade},
            "ocr": {"engine": self.ocr_engine, "model_dir": str(self.ocr_model_dir),
                    "text_score": self.ocr_text_score, "use_cls": self.ocr_use_cls, "auto": self.ocr_auto},
            "asr": {"engine": "sensevoice-onnx", "model_dir": str(self.asr_model_dir),
                    "language": self.asr_language, "use_itn": self.asr_use_itn,
                    "intra_op_num_threads": self.asr_intra_threads, "max_seconds": self.asr_max_seconds},
            "vlm": {"configured": bool(self.vlm_url), "model": self.vlm_model,
                    "protocol": self.vlm_protocol, "bbox_format": self.vlm_bbox_format,
                    "auto": self.vlm_auto,
                    "api_key": "已配置" if self.vlm_api_key else "未配置"},
            "i2v_provider": self.i2v_provider,
            "dashscope": {"configured": bool(self.dashscope_api_key), "model": self.dashscope_model,
                          "region": self.dashscope_region, "resolution": self.dashscope_resolution,
                          "workspace": bool(self.dashscope_workspace),
                          "base_url": "已配置" if self.dashscope_base_url else "默认",
                          "poll_interval": self.dashscope_poll_interval,
                          "max_interval": self.dashscope_max_interval,
                          "api_key": "已配置" if self.dashscope_api_key else "未配置"},
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
        subtitles_enabled=env.flag("PBSC_VIDEO_SUBTITLES", False),
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

    ocr_engine = env.text("PBSC_OCR_ENGINE", "onnxruntime")
    if ocr_engine not in ("onnxruntime",):
        raise ValueError("PBSC_OCR_ENGINE 目前只支持 onnxruntime（本地 RapidOCR）")

    asr_language = env.text("PBSC_ASR_LANGUAGE", "auto")
    if asr_language not in ("auto", "zh", "en", "yue", "ja", "ko", "nospeech"):
        raise ValueError("PBSC_ASR_LANGUAGE 只能是 auto/zh/en/yue/ja/ko/nospeech")

    vlm_protocol = env.text("PBSC_VLM_PROTOCOL", "openai_chat").lower()
    if vlm_protocol not in ("openai_chat", "custom_json"):
        raise ValueError("PBSC_VLM_PROTOCOL 只能是 openai_chat 或 custom_json")
    vlm_bbox_format = env.text("PBSC_VLM_BBOX_FORMAT", "xyxy").lower()
    if vlm_bbox_format not in ("xyxy", "xywh", "xyxy_1000", "xyxy_normalized"):
        raise ValueError("PBSC_VLM_BBOX_FORMAT 只能是 xyxy/xywh/xyxy_1000/xyxy_normalized")

    tts_provider = env.text("PBSC_TTS_PROVIDER", "reference").lower()
    if tts_provider not in ("reference", "cosyvoice"):
        raise ValueError("PBSC_TTS_PROVIDER 只能是 reference 或 cosyvoice")
    cosyvoice_format = env.text("COSYVOICE_FORMAT", "wav").lower()
    if cosyvoice_format not in ("wav", "mp3", "pcm", "opus"):
        raise ValueError("COSYVOICE_FORMAT 只能是 wav/mp3/pcm/opus")
    cosyvoice_language_hints = tuple(
        item.strip() for item in (env.text("COSYVOICE_LANGUAGE_HINTS") or "").split(",") if item.strip())
    cosyvoice_workspace = env.text("COSYVOICE_WORKSPACE_ID") or env.text("DASHSCOPE_WORKSPACE_ID")
    cosyvoice_region = env.text("COSYVOICE_REGION") or env.text("DASHSCOPE_REGION", "cn-beijing")
    default_ws_url = (f"wss://{cosyvoice_workspace}.{cosyvoice_region}.maas.aliyuncs.com/api-ws/v1/inference"
                      if cosyvoice_workspace else "wss://dashscope.aliyuncs.com/api-ws/v1/inference")
    cosyvoice_ws_url = env.text("COSYVOICE_WS_URL", default_ws_url)

    i2v_provider = env.text("PBSC_I2V_PROVIDER", "gateway").lower()
    if i2v_provider not in ("gateway", "dashscope"):
        raise ValueError("PBSC_I2V_PROVIDER 只能是 gateway 或 dashscope")
    dashscope_media_protocol = env.text("PBSC_DASHSCOPE_MEDIA_PROTOCOL", "auto").lower()
    if dashscope_media_protocol not in ("auto", "media", "image_url"):
        raise ValueError("PBSC_DASHSCOPE_MEDIA_PROTOCOL 只能是 auto/media/image_url")
    dashscope_resolution = env.text("PBSC_DASHSCOPE_RESOLUTION", "1080P").upper()
    if dashscope_resolution not in ("480P", "720P", "1080P"):
        raise ValueError("PBSC_DASHSCOPE_RESOLUTION 只能是 480P/720P/1080P")

    return Settings(
        root=ROOT,
        data_dir=data_dir,
        books_dir=data_dir / "books",
        samples_dir=ROOT / "data" / "samples",
        web_dir=env.path("PBSC_WEB_DIR", ROOT / "前端"),
        install_sample=env.flag("PBSC_INSTALL_SAMPLE", True),
        voice_service_url=env.text("PBSC_VOICE_SERVICE_URL"),
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
        pdf_max_mb=max(1, env.integer("PBSC_PDF_MAX_MB", 100)),
        pdf_max_pages=max(1, env.integer("PBSC_PDF_MAX_PAGES", 150)),
        pdf_max_edge=max(2, env.integer("PBSC_PDF_MAX_EDGE", 3000)),
        pdf_max_pixels=max(4, env.integer("PBSC_PDF_MAX_PIXELS", 9000000)),
        task_workers=env.integer("PBSC_TASK_WORKERS", 2),
        video_workers=max(1, env.integer("PBSC_VIDEO_WORKERS", 1)),
        retry_attempts=env.integer("PBSC_RETRY_ATTEMPTS", 3),
        retry_base_delay=env.number("PBSC_RETRY_BASE_DELAY", 1.0),
        request_timeout=env.number("PBSC_REQUEST_TIMEOUT", 120.0),
        narration_max_chars=env.integer("PBSC_NARRATION_MAX_CHARS", 220),
        narration_temperature=env.number("PBSC_NARRATION_TEMPERATURE", 0.3),
        narration_attempts=env.integer("PBSC_NARRATION_ATTEMPTS", 3),
        tts_voice=env.text("PBSC_TTS_VOICE", "xiaosi"),
        ocr_engine=ocr_engine,
        ocr_model_dir=env.path("PBSC_OCR_MODEL_DIR", data_dir / "models" / "rapidocr"),
        ocr_text_score=env.number("PBSC_OCR_TEXT_SCORE", 0.5),
        ocr_use_cls=env.flag("PBSC_OCR_USE_CLS", True),
        ocr_auto=env.flag("PBSC_OCR_AUTO", True),
        asr_model_dir=env.path("PBSC_ASR_MODEL_DIR", data_dir / "models" / "sensevoice"),
        asr_language=asr_language,
        asr_use_itn=env.flag("PBSC_ASR_USE_ITN", True),
        asr_intra_threads=env.integer("PBSC_ASR_INTRA_THREADS", 4),
        asr_max_seconds=env.number("PBSC_ASR_MAX_SECONDS", 60.0),
        vlm_url=(env.text("VLM_API_URL") or env.text("PBSC_VLM_URL")
                 or env.text("IMAGE_ANALYSIS_URL")),
        vlm_api_key=(env.text("VLM_API_KEY") or env.text("PBSC_VLM_API_KEY")
                     or env.text("IMAGE_API_KEY")),
        vlm_model=(env.text("VLM_MODEL") or env.text("PBSC_VLM_MODEL")
                   or env.text("IMAGE_MODEL", "qwen3-vl-30b-a3b-instruct")),
        vlm_protocol=vlm_protocol,
        vlm_bbox_format=vlm_bbox_format,
        vlm_timeout=env.number("PBSC_VLM_TIMEOUT", 120.0),
        vlm_max_image_side=env.integer("PBSC_VLM_MAX_IMAGE_SIDE", 1280),
        vlm_temperature=env.number("PBSC_VLM_TEMPERATURE", 0.0),
        vlm_auto=env.flag("PBSC_VLM_AUTO", False),
        auto_generate=env.flag("PBSC_AUTO_GENERATE", True),
        tts_provider=tts_provider,
        cosyvoice_ws_url=cosyvoice_ws_url,
        cosyvoice_api_key=env.text("COSYVOICE_API_KEY") or env.text("DASHSCOPE_API_KEY"),
        cosyvoice_workspace=cosyvoice_workspace,
        cosyvoice_region=cosyvoice_region,
        cosyvoice_model=env.text("COSYVOICE_MODEL", "cosyvoice-v3-plus"),
        cosyvoice_voice=env.text("COSYVOICE_VOICE", "longanyang"),
        cosyvoice_format=cosyvoice_format,
        cosyvoice_sample_rate=env.integer("COSYVOICE_SAMPLE_RATE", 22050),
        cosyvoice_volume=env.integer("COSYVOICE_VOLUME", 50),
        cosyvoice_rate=env.number("COSYVOICE_RATE", 1.0),
        cosyvoice_pitch=env.number("COSYVOICE_PITCH", 1.0),
        cosyvoice_timeout=env.number("COSYVOICE_TIMEOUT", 60.0),
        cosyvoice_language_hints=cosyvoice_language_hints,
        cosyvoice_instruction=env.text("COSYVOICE_INSTRUCTION"),
        cosyvoice_enable_ssml=env.flag("COSYVOICE_ENABLE_SSML", False),
        i2v_provider=i2v_provider,
        dashscope_api_key=env.text("DASHSCOPE_API_KEY") or env.text("PBSC_DASHSCOPE_API_KEY"),
        dashscope_workspace=(env.text("DASHSCOPE_WORKSPACE") or env.text("DASHSCOPE_WORKSPACE_ID")
                             or env.text("PBSC_DASHSCOPE_WORKSPACE")),
        dashscope_region=env.text("DASHSCOPE_REGION") or env.text("PBSC_DASHSCOPE_REGION", "cn-beijing"),
        dashscope_base_url=env.text("DASHSCOPE_BASE_URL") or env.text("PBSC_DASHSCOPE_BASE_URL"),
        dashscope_model=env.text("PBSC_DASHSCOPE_MODEL", "wan3.0-video"),
        dashscope_resolution=dashscope_resolution,
        dashscope_media_protocol=dashscope_media_protocol,
        dashscope_ratio=env.text("PBSC_DASHSCOPE_RATIO", "adaptive"),
        dashscope_prompt_extend=env.flag("PBSC_DASHSCOPE_PROMPT_EXTEND", True),
        dashscope_watermark=env.flag("PBSC_DASHSCOPE_WATERMARK", False),
        dashscope_timeout=env.number("PBSC_DASHSCOPE_TIMEOUT", 300.0),
        dashscope_poll_interval=env.number("PBSC_DASHSCOPE_POLL_INTERVAL", 1.0),
        dashscope_max_interval=env.number("PBSC_DASHSCOPE_MAX_INTERVAL", 3.0),
        # 0 = 所有页都用云端 AI；N>0 = 只有前 N 页用 AI，其余本地推近；-1 = 全部本地推近。
        i2v_max_pages=max(-1, env.integer("PBSC_I2V_MAX_PAGES", 0)),
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
        qa_top_k=env.integer("PBSC_QA_TOP_K", 4),
        qa_max_chars=env.integer("PBSC_QA_MAX_CHARS", 160),
        qa_temperature=env.number("PBSC_QA_TEMPERATURE", 0.2),
        qa_tts=env.flag("PBSC_QA_TTS", True),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = load_settings()
    settings.ensure_dirs()
    return settings


def reset_settings() -> None:
    """测试或修改环境变量后调用，下次 get_settings() 会重新读取。"""
    get_settings.cache_clear()
