# -*- coding: utf-8 -*-
"""异步任务队列与状态查询。

耗时操作（旁白生成、视频生成）提交后立即返回 task_id，前端轮询 GET /api/v1/tasks/<task_id>。
任务状态：queued → running → succeeded | failed | cancelled。

实现说明：
- 进程内线程池，适合单进程部署（比赛演示环境）。多进程部署时各进程的队列互不可见，需换成外部队列。
- 每次状态变化都落盘到 logs/tasks/<task_id>.json，服务重启后仍可查询历史任务；
  重启时还在排队或执行中的任务会被标记为 failed（错误码 INTERRUPTED），不会假装还在跑。
"""
from __future__ import annotations

import json
import logging
import secrets
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Optional

from .errors import AppError, TaskCancelled
from .run_logger import now_iso

log = logging.getLogger("pbsc.tasks")

FINAL_STATES = ("succeeded", "failed", "cancelled")


class TaskContext:
    """传给任务函数的句柄：汇报进度、检查是否被取消。"""

    def __init__(self, manager: "TaskManager", task_id: str):
        self._manager = manager
        self.task_id = task_id

    def progress(self, fraction: float, message: Optional[str] = None, stage: Optional[str] = None) -> None:
        self._manager._update(self.task_id, progress=max(0.0, min(1.0, float(fraction))),
                              message=message, stage=stage)

    def check_cancelled(self) -> None:
        """长任务在各阶段之间调用；已请求取消时抛出 TaskCancelled。"""
        if self._manager._is_cancel_requested(self.task_id):
            raise TaskCancelled("任务已被取消")


class TaskManager:
    def __init__(self, workers: int = 2, store_dir: Optional[Path] = None):
        self._executor = ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="pbsc-task")
        self._lock = threading.RLock()
        self._tasks: dict[str, dict] = {}
        self._cancel: set[str] = set()
        self._dedupe: dict[str, str] = {}
        self._store_dir = Path(store_dir) if store_dir else None
        if self._store_dir:
            self._store_dir.mkdir(parents=True, exist_ok=True)
            self._recover()

    # ---------- 对外 ----------
    def submit(self, kind: str, fn: Callable[[TaskContext], Any], *, params: Optional[dict] = None,
               book_id: Optional[str] = None, page_no: Optional[int] = None,
               dedupe_key: Optional[str] = None) -> dict:
        """提交任务。dedupe_key 相同且前一个任务尚未结束时，直接返回前一个任务，避免重复花钱。"""
        with self._lock:
            if dedupe_key:
                existing = self._tasks.get(self._dedupe.get(dedupe_key, ""))
                if existing and existing["status"] not in FINAL_STATES:
                    snapshot = dict(existing)
                    snapshot["deduplicated"] = True
                    return snapshot
            task_id = f"task-{time.strftime('%Y%m%dT%H%M%S')}-{secrets.token_hex(3)}"
            task = {
                "task_id": task_id, "kind": kind, "status": "queued", "progress": 0.0, "stage": None,
                "message": "排队中", "book_id": book_id, "page_no": page_no, "params": params or {},
                "result": None, "error": None, "created_at": now_iso(), "started_at": None,
                "finished_at": None, "run_id": None,
            }
            self._tasks[task_id] = task
            if dedupe_key:
                self._dedupe[dedupe_key] = task_id
            self._persist(task)
            snapshot = dict(task)       # 先拍快照再交给线程池，否则返回时任务可能已经开始执行
        self._executor.submit(self._run, task_id, fn)
        return snapshot

    def get(self, task_id: str) -> Optional[dict]:
        with self._lock:
            task = self._tasks.get(task_id)
            return dict(task) if task else None

    def list(self, *, book_id: Optional[str] = None, kind: Optional[str] = None,
             status: Optional[str] = None, limit: int = 50) -> list[dict]:
        with self._lock:
            items = [dict(t) for t in self._tasks.values()
                     if (book_id is None or t["book_id"] == book_id)
                     and (kind is None or t["kind"] == kind)
                     and (status is None or t["status"] == status)]
        items.sort(key=lambda t: t["created_at"], reverse=True)
        return items[:limit]

    def cancel(self, task_id: str) -> Optional[dict]:
        """排队中的任务直接取消；执行中的任务置取消标记，由任务在阶段之间自行停止。"""
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return None
            if task["status"] == "queued":
                task.update(status="cancelled", message="已取消", finished_at=now_iso())
                self._persist(task)
            elif task["status"] == "running":
                self._cancel.add(task_id)
                task["message"] = "已请求取消，等待当前阶段结束"
                self._persist(task)
            return dict(task)

    def wait(self, task_id: str, timeout: float = 60.0, interval: float = 0.05) -> Optional[dict]:
        """测试和脚本用：阻塞到任务结束或超时。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            task = self.get(task_id)
            if task is None or task["status"] in FINAL_STATES:
                return task
            time.sleep(interval)
        return self.get(task_id)

    def shutdown(self, wait: bool = False) -> None:
        self._executor.shutdown(wait=wait, cancel_futures=True)

    # ---------- 内部 ----------
    def _run(self, task_id: str, fn: Callable[[TaskContext], Any]) -> None:
        with self._lock:
            task = self._tasks[task_id]
            if task["status"] != "queued":       # 排队期间已被取消
                return
            task.update(status="running", started_at=now_iso(), message="执行中")
            self._persist(task)
        ctx = TaskContext(self, task_id)
        try:
            result = fn(ctx)
            run_id = result.get("run_id") if isinstance(result, dict) else None
            self._update(task_id, status="succeeded", progress=1.0, message="完成", result=result,
                         run_id=run_id, finished_at=now_iso(), force=True)
        except TaskCancelled:
            self._update(task_id, status="cancelled", message="已取消", finished_at=now_iso(), force=True)
        except AppError as exc:
            log.warning("任务 %s 失败：%s", task_id, exc)
            self._update(task_id, status="failed", message=exc.message, error=exc.to_dict(),
                         finished_at=now_iso(), force=True)
        except Exception as exc:  # 未预料的异常也要落到任务状态里，不能让任务一直显示执行中
            log.error("任务 %s 异常：%s\n%s", task_id, exc, traceback.format_exc())
            self._update(task_id, status="failed", message=str(exc), finished_at=now_iso(), force=True,
                         error={"code": "INTERNAL_ERROR", "message": f"{type(exc).__name__}: {exc}"})
        finally:
            with self._lock:
                self._cancel.discard(task_id)

    def _update(self, task_id: str, force: bool = False, **fields: Any) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            for key, value in fields.items():
                if value is not None or key in ("result", "error"):
                    task[key] = value
            # 进度更新很频繁，1 秒内最多落盘一次；状态变化（force）立即落盘
            last = task.get("_persisted_at", 0.0)
            if force or time.time() - last >= 1.0:
                self._persist(task)

    def _is_cancel_requested(self, task_id: str) -> bool:
        with self._lock:
            return task_id in self._cancel

    def _persist(self, task: dict) -> None:
        if not self._store_dir:
            return
        task["_persisted_at"] = time.time()
        public = {k: v for k, v in task.items() if not k.startswith("_")}
        path = self._store_dir / f"{task['task_id']}.json"
        tmp = path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(public, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            tmp.replace(path)
        except OSError as exc:  # 落盘失败不应影响任务本身
            log.warning("任务状态落盘失败 %s：%s", path, exc)

    def _recover(self) -> None:
        for path in sorted(self._store_dir.glob("task-*.json")):
            try:
                task = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if task.get("status") not in FINAL_STATES:
                task.update(status="failed", message="服务重启，任务中断", finished_at=now_iso(),
                            error={"code": "INTERRUPTED", "message": "服务重启导致任务中断，请重新提交"})
                self._tasks[task["task_id"]] = task
                self._persist(task)
            else:
                self._tasks[task["task_id"]] = task


def public_task(task: dict) -> dict:
    """去掉内部字段，供接口返回。"""
    return {k: v for k, v in task.items() if not k.startswith("_")}
