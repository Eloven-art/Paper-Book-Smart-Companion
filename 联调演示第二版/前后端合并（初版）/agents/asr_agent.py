# -*- coding: utf-8 -*-
"""语音识别节点（C 的语音提问链路）：本地 SenseVoiceSmall ONNX 推理。

- 推理在本地完成（ONNX Runtime CPU），不调用外部服务，不产生费用。
- 运行时封装 `funasr_onnx` 来自 PyPI 0.4.11，源码随项目放在 `third_party/SenseVoice/funasr_onnx/`；
  SenseVoice 上游代码（model.py、utils/ 等）也保留在同目录，便于核对导出流程。
- 模型是 `iic/SenseVoiceSmall` 的 int8 ONNX 导出版，放在 `data/models/sensevoice/`；
  可用 `scripts/download_asr_models.py` 重新下载。运行时不需要 torch / funasr。
- 支持 wav/mp3 等常见格式；非 wav 先用 imageio-ffmpeg 转成 16 kHz 单声道，再送入模型。
- 返回文本、语种、情感、声学事件和 run_id；原始标签保留在 `raw_text`，便于排查。
"""
from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

from server.errors import ASRFailed, ASRUnavailable, ValidationError
from server.run_logger import RunLogger

VENDOR_ROOT = Path(__file__).resolve().parent.parent / "third_party" / "SenseVoice"
if str(VENDOR_ROOT) not in sys.path:
    sys.path.insert(0, str(VENDOR_ROOT))

_MODEL_LABEL = "SenseVoiceSmall int8 (ONNX Runtime)"
_REQUIRED_MODULES = ("onnxruntime", "librosa", "soundfile", "kaldi_native_fbank",
                     "sentencepiece", "jieba", "yaml", "numpy", "scipy")
_MODEL_FILES = ("model_quant.onnx", "config.yaml", "am.mvn",
                "chn_jpn_yue_eng_ko_spectok.bpe.model", "tokens.json")
_LANGUAGES = {"auto", "zh", "en", "yue", "ja", "ko", "nospeech"}
_TAG_RE = re.compile(r"<\|([^|]+)\|>")


def parse_result(raw: str) -> dict:
    """拆出 SenseVoice 的标签：语言、情感、声学事件、文本规范化方式。"""
    raw = raw or ""
    tags = _TAG_RE.findall(raw)
    text = _TAG_RE.sub("", raw).strip()
    return {
        "text": text,
        "language": tags[0] if len(tags) > 0 else None,
        "emotion": tags[1] if len(tags) > 1 else None,
        "event": tags[2] if len(tags) > 2 else None,
        "textnorm": tags[3] if len(tags) > 3 else None,
    }


class ASRAgent:
    """单文件语音识别节点。引擎在第一次真正识别时才加载。"""

    def __init__(self, settings, run_logger: RunLogger):
        self.settings = settings
        self.runs = run_logger
        self._engine = None
        self._engine_error: Optional[str] = None
        self._lock = threading.Lock()
        self._infer_lock = threading.Lock()      # ONNX 会话串行使用

    # ------------------------------------------------------------------ 状态
    def status(self) -> dict:
        """健康检查用：不加载模型，只报告依赖、运行时封装和模型文件是否就绪。"""
        missing = [name for name in _REQUIRED_MODULES if importlib.util.find_spec(name) is None]
        vendor_ready = (VENDOR_ROOT / "funasr_onnx" / "__init__.py").is_file()
        model_dir = Path(self.settings.asr_model_dir)
        present = [name for name in _MODEL_FILES if (model_dir / name).is_file()]
        models_ready = len(present) == len(_MODEL_FILES)
        return {
            "engine": "sensevoice-onnx",
            "model_dir": str(model_dir),
            "language": self.settings.asr_language,
            "available": bool(vendor_ready and not missing and (models_ready or self._engine is not None)),
            "vendor_ready": vendor_ready,
            "missing_dependencies": missing,
            "models_ready": models_ready,
            "model_files": present,
            "loaded": self._engine is not None,
            "error": self._engine_error,
        }

    # ------------------------------------------------------------------ 引擎
    def _load_engine(self):
        if self._engine is not None:
            return self._engine
        with self._lock:
            if self._engine is not None:
                return self._engine
            status = self.status()
            if not status["vendor_ready"]:
                raise ASRUnavailable("SenseVoice 运行时未部署到 third_party/SenseVoice/funasr_onnx",
                                     details=status)
            if status["missing_dependencies"]:
                raise ASRUnavailable(
                    "缺少语音识别依赖：" + "、".join(status["missing_dependencies"])
                    + "。请在项目根目录执行 .venv\\Scripts\\pip install -r requirements.txt",
                    details=status)
            if not status["models_ready"]:
                raise ASRUnavailable(
                    "SenseVoice 模型文件不完整，请运行 scripts/download_asr_models.py",
                    details=status)
            try:
                from funasr_onnx import SenseVoiceSmall
            except Exception as exc:       # noqa: BLE001
                self._engine_error = f"{type(exc).__name__}: {exc}"
                raise ASRUnavailable(f"SenseVoice 运行时导入失败：{exc}", details=status) from exc
            try:
                self._engine = SenseVoiceSmall(
                    str(self.settings.asr_model_dir), batch_size=1, quantize=True,
                    intra_op_num_threads=int(self.settings.asr_intra_threads))
            except Exception as exc:       # noqa: BLE001
                self._engine_error = f"{type(exc).__name__}: {exc}"
                raise ASRUnavailable(f"SenseVoice 初始化失败：{exc}", details=status) from exc
            self._engine_error = None
            return self._engine

    # ------------------------------------------------------------------ 音频
    @staticmethod
    def _audio_info(path: Path):
        import soundfile as sf
        info = sf.info(str(path))
        duration = info.frames / float(info.samplerate or 1)
        return duration, info.samplerate

    def _prepare_audio(self, source: Path, workdir: Path) -> tuple[Path, float, int]:
        """保证送给 funasr_onnx 的是它能稳定读取的音频；返回 (路径, 时长, 采样率)。"""
        try:
            duration, rate = self._audio_info(source)
            if source.suffix.lower() == ".wav":
                return source, duration, rate
        except Exception:       # noqa: BLE001 - 不是 soundfile 能读的 wav，走转码
            duration = rate = None

        try:
            import imageio_ffmpeg
            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception as exc:       # noqa: BLE001
            raise ASRUnavailable("该音频格式需要 ffmpeg 转码，但本机找不到 imageio-ffmpeg",
                                 details={"file": source.name}) from exc
        target = workdir / "asr_input.wav"
        process = subprocess.run(
            [ffmpeg, "-y", "-loglevel", "error", "-i", str(source), "-ac", "1", "-ar", "16000", str(target)],
            capture_output=True, timeout=120)
        if process.returncode != 0 or not target.is_file():
            detail = process.stderr.decode("utf-8", "replace")[-300:]
            raise ASRFailed(f"音频转码失败：{detail}")
        duration, rate = self._audio_info(target)
        return target, duration, rate

    # ------------------------------------------------------------------ 识别
    def transcribe_file(self, source: Path, *, language: Optional[str] = None,
                        book_id: Optional[str] = None, page_no: Optional[int] = None,
                        max_seconds: Optional[float] = None) -> dict:
        source = Path(source)
        if not source.is_file():
            raise ASRFailed("音频文件不存在", details={"path": str(source)})
        language = (language or self.settings.asr_language or "auto").lower()
        if language not in _LANGUAGES:
            raise ValidationError("language 只能是 " + "、".join(sorted(_LANGUAGES)),
                                  details={"field": "language"})
        engine = self._load_engine()
        textnorm = "withitn" if self.settings.asr_use_itn else "woitn"

        with tempfile.TemporaryDirectory(prefix="pbsc_asr_") as tmp:
            audio_path, duration, rate = self._prepare_audio(source, Path(tmp))
            if max_seconds and duration > float(max_seconds):
                raise ValidationError(
                    f"音频时长 {duration:.1f} 秒，超过上限 {float(max_seconds):.0f} 秒",
                    code="AUDIO_TOO_LONG", http_status=413,
                    details={"field": "audio", "duration": round(duration, 2),
                             "max_seconds": float(max_seconds)})
            with self.runs.start("asr", {
                "book_id": book_id, "page_no": page_no, "audio": source.name,
                "duration": round(duration, 2), "sample_rate": rate,
            }) as run:
                run.set_model(_MODEL_LABEL, {"quantize": True, "language": language,
                                             "textnorm": textnorm,
                                             "intra_op_num_threads": int(self.settings.asr_intra_threads)},
                              provider="sensevoice-local-onnx")
                run.add_output(source, role="input_audio")
                started = time.perf_counter()
                try:
                    with self._infer_lock:
                        results = engine([str(audio_path)], language=language, textnorm=textnorm)
                except Exception as exc:       # noqa: BLE001
                    raise ASRFailed(f"SenseVoice 识别失败：{exc}") from exc
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                raw = results[0] if results else ""
                parsed = parse_result(raw)
                try:
                    from funasr_onnx.utils.postprocess_utils import rich_transcription_postprocess
                    rich_text = rich_transcription_postprocess(raw)
                except Exception:       # noqa: BLE001 - 后处理失败不影响原始识别结果
                    rich_text = parsed["text"]
                run.note("result", {**parsed, "raw_text": raw, "rich_text": rich_text,
                                    "elapsed_ms": elapsed_ms})
                run.set_cost({"billable": False, "amount": 0.0, "note": "本地推理"})

        return {
            "text": parsed["text"],
            "raw_text": raw,
            "rich_text": rich_text,
            "language": parsed["language"] or language,
            "emotion": parsed["emotion"],
            "event": parsed["event"],
            "duration": round(duration, 2),
            "elapsed_ms": elapsed_ms,
            "run_id": run.run_id,
            "model": _MODEL_LABEL,
        }
