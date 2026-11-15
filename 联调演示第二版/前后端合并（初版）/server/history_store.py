# -*- coding: utf-8 -*-
"""多模态对话历史存储。

按 session_id 一个 JSONL 文件，落在 data/history/ 下；每行是一轮问答（turn），
包含问题、回答、依据、回答音频地址和画面截图地址。追加写入，服务重启不丢。
问题录音另存在 data/qa/，由接口层负责落盘。
"""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Optional

SESSION_RE = re.compile(r"[A-Za-z0-9._:-]{1,64}")


def check_session_id(value: Optional[str]) -> str:
    """session_id 只允许字母数字和 . _ : -，且不超过 64 字符；不合法直接报错。"""
    text = str(value or "").strip()
    if not SESSION_RE.fullmatch(text):
        from .errors import ValidationError
        raise ValidationError("session_id 只能包含字母、数字和 . _ : -，长度 1–64",
                              details={"field": "session_id"})
    return text


class HistoryStore:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _path(self, session_id: str) -> Path:
        return self.directory / f"{check_session_id(session_id)}.jsonl"

    def add(self, session_id: str, turn: dict) -> None:
        line = json.dumps(turn, ensure_ascii=False) + "\n"
        with self._lock:
            with open(self._path(session_id), "a", encoding="utf-8") as fh:
                fh.write(line)

    def list(self, session_id: str, limit: int = 200) -> list[dict]:
        path = self._path(session_id)
        if not path.is_file():
            return []
        turns: list[dict] = []
        with self._lock:
            with open(path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        turn = json.loads(line)
                    except ValueError:
                        continue        # 坏行跳过，不让一次写入异常拖垮整个会话
                    if isinstance(turn, dict):
                        turns.append(turn)
        return turns[-max(1, int(limit)):]

    def sessions(self) -> list[str]:
        return sorted(p.stem for p in self.directory.glob("*.jsonl"))
