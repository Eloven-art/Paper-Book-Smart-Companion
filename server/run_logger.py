# -*- coding: utf-8 -*-
"""运行记录（共同约定 零.3）：任何一个生成结果都能找到它是怎么来的。

每次生成写一个文件 logs/runs/<日期>/<run_id>.json，并在 logs/runs/index.jsonl 追加一行索引。
记录内容：run_id、时间、输入、提示词版本（含文件哈希）、模型名称及参数、输出文件路径、
耗时、状态、错误、费用、上级 run_id、代码版本。密钥字段写入前会被遮蔽。
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from configs.settings import ROOT, redact

from .envelope import new_run_id

_index_lock = threading.Lock()
_code_version: Optional[str] = None


def code_version() -> str:
    """当前代码的 git 提交号（取不到时为 unknown），用于复现。"""
    global _code_version
    if _code_version is None:
        try:
            out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True,
                                 text=True, timeout=5)
            _code_version = out.stdout.strip() or "unknown"
        except Exception:
            _code_version = "unknown"
    return _code_version


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


class RunRecord:
    """一次生成的记录。用作上下文管理器：正常退出记为 ok，抛异常记为 error 并继续向外抛。"""

    def __init__(self, logger: "RunLogger", kind: str, input: Optional[dict] = None,
                 parent_run_id: Optional[str] = None, run_id: Optional[str] = None):
        self._logger = logger
        self._t0 = time.perf_counter()
        self._closed = False
        self.data: dict[str, Any] = {
            "run_id": run_id or new_run_id(kind),
            "kind": kind,
            "parent_run_id": parent_run_id,
            "status": "running",
            "started_at": now_iso(),
            "finished_at": None,
            "elapsed_ms": None,
            "code_version": code_version(),
            "input": redact(input or {}),
            "prompt": None,
            "model": None,
            "outputs": [],
            "cost": None,
            "notes": {},
            "error": None,
        }

    @property
    def run_id(self) -> str:
        return self.data["run_id"]

    def set_prompt(self, version: str, sha256: Optional[str] = None, rendered: Any = None) -> None:
        """version 是提示词文件名（如 narration_v1）；sha256 用来发现“同名但内容被改过”的情况。"""
        self.data["prompt"] = {"version": version, "sha256": sha256, "rendered": rendered}

    def set_model(self, name: Optional[str], params: Optional[dict] = None, provider: Optional[str] = None) -> None:
        self.data["model"] = {"name": name, "provider": provider, "params": redact(params or {})}

    def add_output(self, path: Any, role: str = "", **extra: Any) -> None:
        p = Path(path)
        entry: dict[str, Any] = {"role": role, "path": _relative(p)}
        if p.is_file():
            entry["bytes"] = p.stat().st_size
            entry["sha256"] = sha256_file(p)
        entry.update(extra)
        self.data["outputs"].append(entry)

    def set_cost(self, cost: Optional[dict]) -> None:
        self.data["cost"] = cost

    def note(self, key: str, value: Any) -> None:
        self.data["notes"][key] = redact(value, key)

    def finish(self, status: str = "ok", error: Optional[BaseException] = None) -> None:
        if self._closed:
            return
        self._closed = True
        self.data["status"] = status
        self.data["finished_at"] = now_iso()
        self.data["elapsed_ms"] = int((time.perf_counter() - self._t0) * 1000)
        if error is not None:
            self.data["error"] = {
                "type": type(error).__name__,
                "code": getattr(error, "code", None),
                "message": str(error),
                "traceback": "".join(traceback.format_exception(type(error), error, error.__traceback__))[-4000:],
            }
        self._logger._write(self)

    def __enter__(self) -> "RunRecord":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.finish("error" if exc else "ok", exc)
        return False


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


class RunLogger:
    def __init__(self, runs_dir: Path):
        self.runs_dir = Path(runs_dir)
        self.runs_dir.mkdir(parents=True, exist_ok=True)

    def start(self, kind: str, input: Optional[dict] = None, parent_run_id: Optional[str] = None,
              run_id: Optional[str] = None) -> RunRecord:
        return RunRecord(self, kind, input, parent_run_id, run_id)

    def _write(self, record: RunRecord) -> None:
        day_dir = self.runs_dir / time.strftime("%Y%m%d")
        day_dir.mkdir(parents=True, exist_ok=True)
        path = day_dir / f"{record.run_id}.json"
        path.write_text(json.dumps(record.data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        d = record.data
        line = {"run_id": d["run_id"], "kind": d["kind"], "status": d["status"], "started_at": d["started_at"],
                "elapsed_ms": d["elapsed_ms"], "parent_run_id": d["parent_run_id"],
                "book_id": d["input"].get("book_id"), "page_no": d["input"].get("page_no"),
                "model": (d["model"] or {}).get("name"), "prompt_version": (d["prompt"] or {}).get("version"),
                "file": _relative(path)}
        with _index_lock:
            with open(self.runs_dir / "index.jsonl", "a", encoding="utf-8") as fh:
                fh.write(json.dumps(line, ensure_ascii=False) + "\n")

    def find(self, run_id: str) -> Optional[dict]:
        """按 run_id 找回完整记录。"""
        for path in self.runs_dir.glob(f"*/{run_id}.json"):
            return json.loads(path.read_text(encoding="utf-8"))
        return None
