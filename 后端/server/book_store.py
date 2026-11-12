# -*- coding: utf-8 -*-
"""绘本与分页数据的读写。

目录结构（每本书一个目录）：
    data/books/<book_id>/book.json      书级信息 + 人物设定表 + 全部分页数据
    data/books/<book_id>/pages/         原始页面图片
    data/books/<book_id>/audio/         旁白配音
    data/books/<book_id>/video/         视频片段、整本视频、片段索引

分页数据字段见 docs/接口约定_v0.1.md 第 4 节（零.1 的临时草案，B 冻结 v1.0 后以 B 为准）。
所有对 book.json 的修改都走 update()，带每本书一把锁并原子写入，避免并发任务互相覆盖。
"""
from __future__ import annotations

import json
import re
import shutil
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from .errors import NotFoundError, ValidationError
from .run_logger import now_iso

SCHEMA_VERSION = "0.1-draft"
BOOK_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def check_book_id(book_id) -> str:
    if not isinstance(book_id, str) or not BOOK_ID_RE.match(book_id):
        raise ValidationError("book_id 只能包含小写字母、数字、下划线和短横线，且不超过 64 个字符",
                              details={"field": "book_id"})
    return book_id


def check_page_no(page_no) -> int:
    if isinstance(page_no, bool):
        raise ValidationError("page_no 应为从 1 开始的整数", details={"field": "page_no"})
    try:
        value = int(page_no)
    except (TypeError, ValueError):
        raise ValidationError("page_no 应为从 1 开始的整数", details={"field": "page_no"}) from None
    if value < 1:
        raise ValidationError("page_no 应为从 1 开始的整数", details={"field": "page_no"})
    return value


def empty_page(book_id: str, page_no: int, page_image: str, width: int, height: int) -> dict:
    """新上传页面的初始分页数据：内容理解字段留空，等 B 的 OCR/VLM 模块填充。"""
    return {
        "book_id": book_id,
        "page_no": page_no,
        "page_image": page_image,
        "image_size": {"width": width, "height": height},
        "ocr_text": {"full_text": "", "blocks": []},
        "narration": "",
        "characters": [],
        "objects": [],
        "scene_summary": "",
        "plot_events": [],
        "video_clip_id": None,
        "time_range": None,
        "keyframes": [],
        "source_reference": {"origin": "upload"},
        "safety_check": {"status": "pending_review", "age_range": None, "reviewer": None, "notes": ""},
    }


class BookStore:
    def __init__(self, books_dir: Path):
        self.books_dir = Path(books_dir)
        self.books_dir.mkdir(parents=True, exist_ok=True)
        self._locks: dict[str, threading.RLock] = {}
        self._locks_guard = threading.Lock()

    def _lock(self, book_id: str) -> threading.RLock:
        with self._locks_guard:
            return self._locks.setdefault(book_id, threading.RLock())

    def book_dir(self, book_id: str) -> Path:
        return self.books_dir / check_book_id(book_id)

    def exists(self, book_id: str) -> bool:
        return (self.book_dir(book_id) / "book.json").is_file()

    def list_ids(self) -> list[str]:
        return sorted(p.parent.name for p in self.books_dir.glob("*/book.json") if BOOK_ID_RE.match(p.parent.name))

    def load(self, book_id: str) -> dict:
        path = self.book_dir(book_id) / "book.json"
        if not path.is_file():
            raise NotFoundError(f"找不到绘本 {book_id}", code="BOOK_NOT_FOUND", details={"book_id": book_id})
        with self._lock(book_id):
            return json.loads(path.read_text(encoding="utf-8"))

    def save(self, book: dict) -> None:
        book_id = check_book_id(book.get("book_id"))
        directory = self.book_dir(book_id)
        directory.mkdir(parents=True, exist_ok=True)
        book["updated_at"] = now_iso()
        with self._lock(book_id):
            tmp = directory / "book.json.tmp"
            tmp.write_text(json.dumps(book, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(directory / "book.json")

    def update(self, book_id: str, mutate: Callable[[dict], None]) -> dict:
        """在锁内完成 读取 → 修改 → 写回，返回修改后的整本数据。"""
        with self._lock(book_id):
            book = self.load(book_id)
            mutate(book)
            self.save(book)
            return book

    @staticmethod
    def get_page(book: dict, page_no: int) -> dict:
        for page in book.get("pages", []):
            if page.get("page_no") == page_no:
                return page
        raise NotFoundError(f"绘本 {book.get('book_id')} 没有第 {page_no} 页", code="PAGE_NOT_FOUND",
                            details={"book_id": book.get("book_id"), "page_no": page_no})

    def create(self, title: str, book_id: Optional[str] = None) -> dict:
        book_id = check_book_id(book_id) if book_id else f"book-{time.strftime('%Y%m%d%H%M%S')}"
        with self._lock(book_id):
            if self.exists(book_id):
                raise ValidationError(f"绘本 {book_id} 已存在", code="BOOK_EXISTS", http_status=409)
            book = {"schema_version": SCHEMA_VERSION, "book_id": book_id, "title": title or book_id,
                    "language": "zh", "age_range": None, "created_at": now_iso(),
                    "source_reference": {"origin": "upload"}, "characters": [], "pages": [], "video": None}
            self.save(book)
            return book

    def install_sample(self, sample_dir: Path, overwrite: bool = False) -> str:
        """把示例绘本复制到运行目录，便于在它上面试跑真实生成链路。"""
        meta = json.loads((sample_dir / "book.json").read_text(encoding="utf-8"))
        book_id = check_book_id(meta["book_id"])
        target = self.book_dir(book_id)
        with self._lock(book_id):
            if target.exists():
                if not overwrite:
                    return book_id
                shutil.rmtree(target)
            shutil.copytree(sample_dir, target, ignore=shutil.ignore_patterns("video", "audio"))
            book = json.loads((target / "book.json").read_text(encoding="utf-8"))
            book["video"] = None
            for page in book.get("pages", []):
                # 示例里的视频信息属于假接口的固定数据，真实链路从空白开始生成
                page["video_clip_id"], page["time_range"], page["keyframes"] = None, None, []
                for key in ("video_prompt", "video_meta", "narration_meta"):
                    page.pop(key, None)
            self.save(book)
        return book_id

    def resolve_media(self, book_id: str, relative: str) -> Path:
        """把接口里的相对路径解析成磁盘路径，并确保不会跳出该绘本目录。"""
        base = self.book_dir(book_id).resolve()
        target = (base / relative).resolve()
        if base != target and base not in target.parents:
            raise ValidationError("非法的文件路径", code="INVALID_PATH")
        if not target.is_file():
            raise NotFoundError("文件不存在", code="FILE_NOT_FOUND", details={"path": relative})
        return target


def summarize_book(book: dict, media_url: Callable[[str], str]) -> dict:
    """书架列表里的一行。"""
    pages = book.get("pages", [])
    video = book.get("video") or {}
    return {
        "book_id": book["book_id"],
        "title": book.get("title"),
        "page_count": len(pages),
        "age_range": book.get("age_range"),
        "cover_image_url": media_url(pages[0]["page_image"]) if pages else None,
        "has_narration": bool(pages) and all(p.get("narration") for p in pages),
        "video": {"ready": bool(video.get("path")), "url": media_url(video["path"]) if video.get("path") else None,
                  "duration": video.get("duration")},
    }


def with_urls(page: dict, media_url: Callable[[str], str]) -> dict:
    """给分页数据补上可直接访问的地址（不改动落盘的数据）。"""
    out = dict(page)
    out["page_image_url"] = media_url(page["page_image"]) if page.get("page_image") else None
    meta = page.get("video_meta") or {}
    out["video_clip_url"] = media_url(meta["clip_path"]) if meta.get("clip_path") else None
    audio = (page.get("narration_audio") or {}).get("path")
    out["narration_audio_url"] = media_url(audio) if audio else None
    return out
