# -*- coding: utf-8 -*-
"""把配置、存储、日志、台账和各生成节点装配到一起。服务端和命令行脚本都从这里取用同一套对象。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from configs.settings import Settings, get_settings
from server.book_store import BookStore
from server.run_logger import RunLogger

from .cost import CostLedger
from .story_agent import NarrationAgent
from .video_agent import VideoAgent


@dataclass
class Pipeline:
    settings: Settings
    store: BookStore
    runs: RunLogger
    ledger: CostLedger
    narration: NarrationAgent
    video: VideoAgent


def build_pipeline(settings: Optional[Settings] = None) -> Pipeline:
    settings = settings or get_settings()
    settings.ensure_dirs()
    store = BookStore(settings.books_dir)
    runs = RunLogger(settings.runs_dir)
    ledger = CostLedger(settings.ledger_file, settings.pricing_file)
    return Pipeline(settings=settings, store=store, runs=runs, ledger=ledger,
                    narration=NarrationAgent(settings, store, runs, ledger),
                    video=VideoAgent(settings, store, runs, ledger))
