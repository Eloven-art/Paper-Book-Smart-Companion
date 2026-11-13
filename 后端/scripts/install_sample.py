#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把仓库自带的示例绘本（data/samples/demo_book）装回运行目录 data/books/。

data/books/ 是运行数据，不进 git；被清掉（比如误点 Discard）之后页面上的
“朵朵找月亮”就会报 BOOK_NOT_FOUND，跑这个脚本就能恢复。

用法（仓库根目录）：
    python scripts/install_sample.py
    python scripts/install_sample.py --overwrite     # 已经存在也重新装一遍
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from configs.settings import get_settings          # noqa: E402
from server.book_store import BookStore            # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="装回示例绘本")
    parser.add_argument("--overwrite", action="store_true", help="已经存在也重新安装")
    args = parser.parse_args()

    settings = get_settings()
    sample = settings.samples_dir / "demo_book"
    if not sample.is_dir():
        print(f"[X] 找不到样本目录：{sample}", file=sys.stderr)
        return 1

    store = BookStore(settings.books_dir)
    book_id = store.install_sample(sample, overwrite=args.overwrite)
    book = store.load(book_id)
    pages = book.get("pages") or []
    objects = sum(len(p.get("objects") or []) for p in pages)
    print(f"已装好：{book_id}《{book.get('title')}》{len(pages)} 页，物体框共 {objects} 个")
    print(f"位置：{settings.books_dir / book_id}")
    print(f"页面：http://127.0.0.1:8000/api/v1/books/{book_id}/pages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
