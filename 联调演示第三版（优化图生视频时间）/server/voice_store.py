# -*- coding: utf-8 -*-
"""本机音色选择状态（不存密钥）。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .run_logger import now_iso


def load_selection(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def selected_voice_id(path: Path) -> Optional[str]:
    value = load_selection(path).get("voice_id")
    return value if isinstance(value, str) and value else None


def save_selection(path: Path, voice_id: str, *, model: Optional[str] = None,
                   name: Optional[str] = None) -> dict:
    data = {"voice_id": voice_id, "model": model, "name": name, "updated_at": now_iso()}
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return data
