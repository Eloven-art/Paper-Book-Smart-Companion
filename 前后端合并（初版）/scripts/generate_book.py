#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""命令行跑生成链路（不需要启动服务）：旁白 → 配音 → 图生视频 → 拼接。

用法（在仓库根目录）：
    python scripts/generate_book.py --book demo_book                      # 旁白 + 视频，全书
    python scripts/generate_book.py --book demo_book --steps narration    # 只生成旁白
    python scripts/generate_book.py --book demo_book --steps video --page 2   # 只生成第 2 页的成片
    python scripts/generate_book.py --book demo_book --force              # 忽略缓存重新生成（会重新花钱）
    python scripts/generate_book.py --costs                               # 查看费用台账汇总

demo_book 是仓库自带的自制模拟绘本，首次使用时会自动复制到 data/books/ 下。
外部服务没有配置时自动走降级方案（原文朗读、占位提示音、静态页图推近），输出里会标明。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from agents.pipeline import build_pipeline  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="命令行跑旁白与视频生成链路")
    parser.add_argument("--book", help="绘本 ID，例如 demo_book")
    parser.add_argument("--steps", default="narration,video", help="要执行的步骤，逗号分隔：narration,video")
    parser.add_argument("--page", type=int, help="只处理这一页（视频步骤下只生成该页成片，不拼整本）")
    parser.add_argument("--force", action="store_true", help="忽略缓存重新生成")
    parser.add_argument("--costs", action="store_true", help="只打印费用台账汇总")
    args = parser.parse_args()

    pipe = build_pipeline()
    if args.costs:
        print(json.dumps({"今天": pipe.ledger.summary(time.strftime("%Y-%m-%d")), "累计": pipe.ledger.summary()},
                         ensure_ascii=False, indent=2))
        return 0
    if not args.book:
        parser.error("请用 --book 指定绘本 ID")

    sample = pipe.settings.samples_dir / args.book
    if not pipe.store.exists(args.book) and (sample / "book.json").is_file():
        pipe.store.install_sample(sample)
        print(f"已把示例绘本复制到 {pipe.store.book_dir(args.book)}")

    services = pipe.settings.public_summary()["services"]
    print("外部服务：" + "，".join(f"{k} {'已配置' if v['configured'] else '未配置（走降级方案）'}"
                               for k, v in services.items()))

    def progress(fraction: float, message: str) -> None:
        print(f"  [{fraction * 100:5.1f}%] {message}", flush=True)

    steps = [s.strip() for s in args.steps.split(",") if s.strip()]
    started = time.perf_counter()
    if "narration" in steps:
        result = pipe.narration.generate_book(args.book, [args.page] if args.page else None, progress)
        print(f"\n旁白完成（run_id={result['run_id']}）")
        for p in result["pages"]:
            flag = "，需人工复核" if p["needs_review"] else ""
            print(f"  第 {p['page_no']} 页 [{p['method']}{flag}] {p['narration']}")
    if "video" in steps:
        if args.page:
            clip = pipe.video.generate_page_clip(args.book, args.page, args.force,
                                                 notify=lambda m: print("  " + m, flush=True))
            print(f"\n第 {args.page} 页成片：{pipe.store.book_dir(args.book) / clip['clip_path']}"
                  f"（{clip['duration']} 秒，{clip['provider']}{'，降级片段' if clip['is_fallback'] else ''}）")
        else:
            result = pipe.video.generate_book_video(args.book, args.force, progress)
            print(f"\n整本视频：{pipe.store.book_dir(args.book) / result['video_path']}（{result['duration']} 秒，"
                  f"run_id={result['run_id']}）")
            for c in result["clips"]:
                print(f"  第 {c['page_no']} 页 {c['time_range'][0]:.2f}–{c['time_range'][1]:.2f} 秒  "
                      f"{c['provider']}{'（降级片段）' if c['is_fallback'] else ''}")
            if result["fallback_pages"]:
                print(f"  注意：第 {result['fallback_pages']} 页用的是本地降级片段，不是 AI 生成的视频")
            if result["placeholder_audio_pages"]:
                print(f"  注意：第 {result['placeholder_audio_pages']} 页用的是占位提示音，不是真实配音")
    print(f"\n总耗时 {time.perf_counter() - started:.1f} 秒；运行记录在 {pipe.settings.runs_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
