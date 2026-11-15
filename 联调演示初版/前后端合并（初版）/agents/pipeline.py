# -*- coding: utf-8 -*-
"""把配置、存储、日志、台账和各生成节点装配到一起。服务端和命令行脚本都从这里取用同一套对象。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from configs.settings import Settings, get_settings
from server.book_store import BookStore
from server.history_store import HistoryStore
from server.run_logger import RunLogger

from .asr_agent import ASRAgent
from .cosyvoice_voice import CosyVoiceVoiceManager
from .cost import CostLedger
from .ocr_agent import OCRAgent
from .qa_agent import QAAgent
from .story_agent import NarrationAgent
from .tts_client import build_tts_client
from .video_agent import VideoAgent
from .vlm_agent import VLMAgent


@dataclass
class Pipeline:
    settings: Settings
    store: BookStore
    runs: RunLogger
    ledger: CostLedger
    ocr: OCRAgent
    asr: ASRAgent
    vlm: VLMAgent
    narration: NarrationAgent
    tts: object
    voices: CosyVoiceVoiceManager
    video: VideoAgent
    qa: QAAgent
    history: HistoryStore


def build_pipeline(settings: Optional[Settings] = None) -> Pipeline:
    settings = settings or get_settings()
    settings.ensure_dirs()
    store = BookStore(settings.books_dir)
    runs = RunLogger(settings.runs_dir)
    ledger = CostLedger(settings.ledger_file, settings.pricing_file)
    tts = build_tts_client(settings)
    voices = CosyVoiceVoiceManager(
        settings.cosyvoice_api_key, settings.cosyvoice_workspace, settings.cosyvoice_region,
        settings.cosyvoice_model, settings.cosyvoice_timeout)
    return Pipeline(settings=settings, store=store, runs=runs, ledger=ledger,
                    ocr=OCRAgent(settings, store, runs),
                    asr=ASRAgent(settings, runs),
                    vlm=VLMAgent(settings, store, runs),
                    narration=NarrationAgent(settings, store, runs, ledger),
                    tts=tts, voices=voices,
                    video=VideoAgent(settings, store, runs, ledger, tts=tts),
                    qa=QAAgent(settings, store, runs, ledger, tts=tts),
                    history=HistoryStore(settings.history_dir))
