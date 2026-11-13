#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 VLM 的画面理解插进「OCR 对比」那张表里，算同一套 IoU。

背景：队友已经比过 RapidOCR 和 PaddleOCR（demo_book 第 1 页，人工标注为真值）。
这里不再重复跑那两个，只把第三条路 —— 视觉大模型 —— 用同样的真值、同样的算法算一遍，
让说明书里能回答「既然有专用 OCR，为什么还要上一次 VLM」。

用法（仓库根目录，后端要已经在跑）：
    python eval/compare_vlm_ocr.py
    python eval/compare_vlm_ocr.py --page 1 --keep-book
    python eval/compare_vlm_ocr.py --gt eval/gold.json      # 换成更大的人工标注

它做的事：
1. 把 data/books/demo_book 的页面图重新上传成一本新书（不覆盖队友的示例数据）；
2. 触发画面理解，等任务结束；
3. 取 VLM 的 ocr_text.blocks，和人工标注框算 IoU；
4. 和队友已经算好的 RapidOCR / PaddleOCR 数字拼成一张 Markdown 表。

注意：人工真值目前只有第 1 页的两行文字（来自队友的对比页）。样本很小，
结论只能写成「方法验证」，不能写成「全面评测」——表下面那句话别删。
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

PAGES_DIR = ROOT / "data" / "books" / "demo_book" / "pages"

# 人工标注真值（demo_book 第 1 页，1280x720），来自队友的对比页
GOLD = {
    1: [[272, 616, 1008, 649], [416, 660, 864, 692]],
}

# 队友的框和真值都在 OCR对比_可交互.html 里，这里用同一套 IoU 重算过，
# 结果和队友报告的平均值完全一致（Rapid 0.719 / Paddle 0.814），所以可以放心并列。
BASELINES = [
    {"name": "RapidOCR", "line_iou": [0.733, 0.705], "mean": 0.719, "cost": "2.9 s/页",
     "note": "30MB 依赖，框略偏内"},
    {"name": "PaddleOCR", "line_iou": [0.869, 0.759], "mean": 0.814, "cost": "13-22 s/页",
     "note": "1GB 依赖，框更贴人工标注"},
]


def iou(a, b) -> float:
    """两个 [x1,y1,x2,y2] 的交并比。"""
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    if inter <= 0:
        return 0.0
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def match_iou(gold: list, pred: list) -> list[float]:
    """按顺序给每个人工框找 IoU 最高的预测框，每个预测框只用一次。"""
    remaining = list(range(len(pred)))
    scores = []
    for target in gold:
        best, best_i = 0.0, None
        for i in remaining:
            score = iou(target, pred[i])
            if score > best:
                best, best_i = score, i
        if best_i is not None:
            remaining.remove(best_i)
        scores.append(round(best, 3))
    return scores


def api(base: str, path: str, method: str = "GET", **kwargs):
    import requests
    resp = requests.request(method, base.rstrip("/") + "/api/v1" + path, timeout=300, **kwargs)
    body = resp.json() if resp.content else {}
    if resp.status_code >= 400 or body.get("status") != "ok":
        raise SystemExit(f"[X] {method} {path} 失败 {resp.status_code}：{resp.text[:300]}")
    return body["data"]


def wait_task(base: str, task_id: str, timeout: float = 300.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        task = api(base, f"/tasks/{task_id}")
        print(f"      {task['status']} {int((task.get('progress') or 0) * 100)}% {task.get('message') or ''}")
        if task["status"] in ("succeeded", "failed", "cancelled"):
            return task
        time.sleep(2)
    raise SystemExit("[X] 任务超时")


def main() -> int:
    parser = argparse.ArgumentParser(description="VLM 与专用 OCR 的同表对比")
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--page", type=int, default=1, help="用人工标注的那一页（默认 1）")
    parser.add_argument("--gt", help="人工标注 JSON：{\"1\": [[x1,y1,x2,y2], ...]}")
    parser.add_argument("--keep-book", action="store_true", help="跑完保留上传的书，方便在页面上看")
    args = parser.parse_args()

    gold = GOLD
    if args.gt:
        gold = {int(k): v for k, v in json.loads(Path(args.gt).read_text(encoding="utf-8")).items()}
    if args.page not in gold:
        raise SystemExit(f"[X] 没有第 {args.page} 页的人工标注")

    images = sorted(PAGES_DIR.glob("page_*.png"))
    if not images:
        raise SystemExit(f"[X] 找不到示例页图：{PAGES_DIR}")

    book_id = f"eval-{time.strftime('%H%M%S')}"
    print(f"[1/4] 上传 {len(images)} 页到新书 {book_id}（不动队友的示例数据）...")
    files = [("pages", (p.name, p.read_bytes(), "image/png")) for p in images]
    created = api(args.base, "/books", "POST", data={"title": "VLM 对比用书", "book_id": book_id}, files=files)
    if not created.get("task_id"):
        raise SystemExit("[X] 没有 task_id：后端没配 VLM，无法对比")
    print(f"      task_id={created['task_id']}")

    print("[2/4] 等画面理解跑完...")
    task = wait_task(args.base, created["task_id"])
    if task["status"] != "succeeded":
        raise SystemExit(f"[X] 任务失败：{task.get('error')}")

    print(f"[3/4] 取第 {args.page} 页的 VLM 文字框，和人工标注算 IoU...")
    page = api(args.base, f"/books/{book_id}/pages/{args.page}")
    blocks = (page.get("ocr_text") or {}).get("blocks") or []
    pred = [b["bbox"] for b in blocks if b.get("bbox")]
    if not pred:
        raise SystemExit("[X] VLM 没给文字框，无法算 IoU")
    scores = match_iou(gold[args.page], pred)
    mean = round(sum(scores) / len(scores), 3)
    elapsed = ((page.get("understanding_meta") or {}).get("elapsed_ms") or 0) / 1000
    usage = (page.get("understanding_meta") or {}).get("usage") or {}

    print("[4/4] 结果\n")
    print("| 方案 | " + " | ".join(f"第{i + 1}行 IoU" for i in range(len(scores))) + " | 平均 IoU | 单页耗时 | 备注 |")
    print("|---|---|---|---|---|")
    print("| 人工标注（真值） | " + " | ".join("1.000" for _ in scores) + " | 1.000 | — | 基准 |")
    for base in BASELINES:
        cells = ([f"{v:.3f}" for v in base["line_iou"]]
                 if base.get("line_iou") and len(base["line_iou"]) == len(scores) else ["—"] * len(scores))
        print(f"| {base['name']} | " + " | ".join(cells) + f" | {base['mean']:.3f} | {base['cost']} | {base['note']} |")
    print("| **GLM-4.6V-Flash（VLM）** | " + " | ".join(f"{v:.3f}" for v in scores) +
          f" | {mean:.3f} | {elapsed:.1f}s | 免费额度；同一次调用还产出物体框/摘要/情节 |")

    print(f"\nVLM 原始文字块 {len(pred)} 个，token 用量 {usage}，物体 {len(page.get('objects') or [])} 个。")
    print("注意：人工标注只有这几行，样本量小，只能作为方法验证，不能当作全面评测结论。")
    if args.keep_book:
        print(f"\n保留的书：{args.base}/api/v1/books/{book_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
