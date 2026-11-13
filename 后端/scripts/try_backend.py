#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""跑通「上传绘本 → 画面理解 → 点击识物」整条链路。

前提：后端已经在另一个窗口跑起来（.venv\\Scripts\\python -m server）。

用法（仓库根目录，另开一个窗口）：
    python scripts/try_backend.py
    python scripts/try_backend.py --base http://127.0.0.1:8000 --pages 3
    python scripts/try_backend.py --book demo_book     # 只针对已有绘本重跑理解，不上传
    python scripts/try_backend.py --book try-233733 --page 2 --qa "画面上有什么？"

脚本会依次做：
    1. GET  /api/v1/health        确认服务活着
    2. POST /api/v1/books         上传示例绘本的 1-3 页，拿到 task_id
    3. GET  /api/v1/tasks/<id>    轮询到 succeeded / failed，打印阶段
    4. GET  /api/v1/books/<id>/pages/1   看 OCR、摘要、物体框
    5. POST /api/v1/touch/identify       点第一个物体的框心，再点一次空白处
    6. 可选 --qa：POST /api/v1/qa/ask    问一句，打印回答和依据
只依赖 requests。
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


def line(char: str = "-", width: int = 62) -> None:
    print(char * width)


def die(message: str) -> int:
    print(f"\n[X] {message}", file=sys.stderr)
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="联调：上传 → 理解 → 识物")
    parser.add_argument("--base", default="http://127.0.0.1:8000", help="后端地址")
    parser.add_argument("--pages", type=int, default=2, help="上传几页（默认 2）")
    parser.add_argument("--book", help="不上传，直接对已有 book_id 重跑理解")
    parser.add_argument("--page", type=int, help="配合 --book：只重跑这一页")
    parser.add_argument("--qa", metavar="问题", help='顺便问一句，例如 --qa "画面上有什么？"')
    parser.add_argument("--force", action="store_true", help="配合 --book：已经有理解结果的页也重跑")
    parser.add_argument("--timeout", type=float, default=300.0, help="等任务的最长秒数")
    args = parser.parse_args()

    import requests

    api = args.base.rstrip("/") + "/api/v1"

    # ---------- 1. 服务在不在 ----------
    print(f"[1/5] 检查服务 {api}/health ...")
    try:
        resp = requests.get(f"{api}/health", timeout=5)
    except requests.RequestException as exc:
        return die(f"连不上后端（{type(exc).__name__}）。\n"
                   f"    先另开一个窗口，在仓库根目录执行：\n"
                   f"        .venv\\Scripts\\python -m server\n"
                   f"    那个窗口要一直开着；看到 Running on http://127.0.0.1:8000 再回来跑本脚本。")
    if resp.status_code != 200:
        return die(f"/health 返回 {resp.status_code}：{resp.text[:200]}")
    health = resp.json().get("data", {})
    services = health.get("services", {})
    print(f"      服务在线，已配置的外部服务：",
          {k: v.get("configured") for k, v in services.items()})
    if not services.get("vlm", {}).get("configured"):
        print("      [!] VLM 没配置，上传后不会自动理解。检查 .env 里的 IMAGE_ANALYSIS_URL / IMAGE_API_KEY")

    # ---------- 2. 上传 ----------
    if args.book:
        book_id = args.book
        print(f"\n[2/5] 跳过上传，直接对已有绘本 {book_id} 重跑画面理解"
              f"{f'（只跑第 {args.page} 页）' if args.page else ''}")
        payload = {"force": bool(args.force)}
        if args.page:
            payload["page_no"] = args.page
        print(f"      force={bool(args.force)}（不加 --force 时，已理解过的页会直接跳过）")
        resp = requests.post(f"{api}/books/{book_id}/understand", json=payload, timeout=30)
        if resp.status_code == 501:
            return die("后端说画面理解未接入（501）：VLM 没配置，或个人接口没启用。")
        if resp.status_code >= 400:
            return die(f"重跑理解失败 {resp.status_code}：{resp.text[:300]}")
        task = resp.json()["data"]
    else:
        images = sorted(PAGES_DIR.glob("page_*.png"))[: max(1, args.pages)]
        if not images:
            return die(f"找不到示例页图：{PAGES_DIR}")
        book_id = f"try-{time.strftime('%H%M%S')}"
        print(f"\n[2/5] 上传 {len(images)} 页，book_id={book_id} ...")
        files = [("pages", (p.name, p.read_bytes(), "image/png")) for p in images]
        resp = requests.post(f"{api}/books", data={"title": "联调测试绘本", "book_id": book_id},
                             files=files, timeout=120)
        if resp.status_code >= 400:
            return die(f"上传失败 {resp.status_code}：{resp.text[:300]}")
        created = resp.json()["data"]
        task = {"task_id": created.get("task_id")}
        print(f"      已保存 {created['page_count']} 页；task_id={task['task_id']}")
        if not task["task_id"]:
            print("      [!] 没拿到 task_id：说明 VLM 未配置，画面理解不会自动跑")

    # ---------- 3. 等任务 ----------
    if task.get("task_id"):
        print(f"\n[3/5] 轮询任务（免费模型可能要十几秒，最长等 {args.timeout:.0f} 秒）...")
        deadline = time.time() + args.timeout
        last = None
        while time.time() < deadline:
            resp = requests.get(f"{api}/tasks/{task['task_id']}", timeout=15)
            data = resp.json().get("data", {})
            status = data.get("status")
            message = f"{status} {int((data.get('progress') or 0) * 100)}% {data.get('message') or ''}"
            if message != last:
                print(f"      {message}")
                last = message
            if status in ("succeeded", "failed", "cancelled"):
                if status != "succeeded":
                    return die(f"任务{status}：{json.dumps(data.get('error'), ensure_ascii=False)}")
                print(f"      结果：{json.dumps(data.get('result'), ensure_ascii=False)[:300]}")
                break
            time.sleep(1.5)
        else:
            return die(f"等了 {args.timeout:.0f} 秒任务还没结束，去任务接口手查一次")
    else:
        print("\n[3/5] 没有任务，跳过轮询")

    # ---------- 4. 看这一页 ----------
    page_no = args.page or 1
    print(f"\n[4/5] 读第 {page_no} 页的分页数据 ...")
    resp = requests.get(f"{api}/books/{book_id}/pages/{page_no}", timeout=30)
    if resp.status_code >= 400:
        return die(f"读页面失败 {resp.status_code}：{resp.text[:300]}")
    page = resp.json()["data"]
    ocr = page.get("ocr_text") or {}
    objects = page.get("objects") or []
    print(f"      full_text    : {ocr.get('full_text', '')[:70]}")
    print(f"      scene_summary: {page.get('scene_summary', '')[:70]}")
    print(f"      objects      : {len(objects)} 个")
    for item in objects:
        print(f"        - {item.get('name')} {item.get('bbox')}")
    meta = page.get("understanding_meta") or {}
    if meta:
        print(f"      meta         : model={meta.get('model')} 修正框={meta.get('bbox_adjusted')} "
              f"用量={meta.get('usage')}")

    # ---------- 5. 点击识物 ----------
    print("\n[5/5] 点击识物 ...")
    if not objects:
        return die("没有物体框，识物无从查起（先确认第 3 步成功、objects 不为空）")
    first = objects[0]
    x1, y1, x2, y2 = first["bbox"]
    point = {"book_id": book_id, "page_no": page_no, "x": (x1 + x2) / 2, "y": (y1 + y2) / 2}
    resp = requests.post(f"{api}/touch/identify", json=point, timeout=15)
    if resp.status_code >= 400:
        return die(f"识物失败 {resp.status_code}：{resp.text[:300]}")
    hit = resp.json()["data"]
    print(f"      点 ({point['x']:.0f}, {point['y']:.0f}) → hit={hit['hit']} "
          f"name={(hit.get('object') or {}).get('name')} 候选={hit['candidates']} "
          f"耗时={hit['timings']['lookup_ms']}ms")
    miss = requests.post(f"{api}/touch/identify",
                         json={"book_id": book_id, "page_no": page_no, "x": 2, "y": 2}, timeout=15)
    print(f"      点左上角 (2, 2) → hit={miss.json()['data']['hit']}（应该是 False）")

    line("=")
    print("走通了：上传 → 画面理解 → 读分页 → 点击识物。")

    if args.qa:
        print(f"\n[附加] 提问：{args.qa}")
        resp = requests.post(f"{api}/qa/ask",
                             json={"book_id": book_id, "page_no": page_no, "question": args.qa},
                             timeout=180)
        if resp.status_code >= 400:
            print(f"      问答失败 {resp.status_code}：{resp.text[:300]}")
        else:
            turn = resp.json()["data"]
            print(f"      回答  : {turn['answer_text']}")
            print(f"      有依据: {turn['grounded']}  用时 {turn['timings']['llm_ms']} ms")
            for item in turn.get("evidence") or []:
                print(f"      依据  : 第 {item.get('page_no')} 页「{item.get('quote')}」")

    print(f"浏览器打开 http://127.0.0.1:8000/api/v1/books/{book_id}/pages 可以看到完整数据。")
    line("=")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
