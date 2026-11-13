#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用一张绘本页图验证视觉模型接口是否打通（默认智谱免费模型）。

用法（在仓库根目录）：
    python scripts/check_vlm.py                     # 示例绘本第 1 页，问画面里有什么
    python scripts/check_vlm.py --json              # 按分页数据的字段要结构化 JSON
    python scripts/check_vlm.py 图片路径 "你的问题"
    python scripts/check_vlm.py --model glm-4.6v-flash,glm-4.1v-thinking-flash
    python scripts/check_vlm.py --attempts 3 --wait 5

免费模型按「账户并发」限流，忙的时候会返回 429 + 1305（平台访问量过大）或 1302（触发速率限制）。
本脚本会自动退避重试，并且在一个模型持续不可用时按顺序换下一个免费模型。

环境变量（可写在仓库根目录的 .env 里，不要写进代码）：
    PBSC_VLM_URL / PBSC_VLM_API_KEY / PBSC_VLM_MODEL
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
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

DEFAULT_IMAGE = ROOT / "data" / "books" / "demo_book" / "pages" / "page_001.png"
# 智谱目前免费用、且能看图的三个模型，按能力从新到旧
FALLBACK_MODELS = ["glm-4.6v-flash", "glm-4.1v-thinking-flash", "glm-4v-flash"]
BUSY_CODES = {"1302", "1305"}          # 1302=触发速率限制，1305=平台访问量过大
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
FATAL_STATUS = {401, 403}              # Key 不对，换模型也没用

JSON_PROMPT = """看这一页儿童绘本图片，只输出 JSON，不要解释文字。字段：
{"full_text": "页面上的全部文字", "scene_summary": "一句话描述画面",
 "objects": [{"name": "物体名", "bbox": [左上x, 左上y, 右下x, 右下y]}],
 "safety_notes": "需要人工复核的地方，没有就填空字符串"}
bbox 用图片像素坐标，objects 只列 3-6 个孩子会想点的名词实体（不要天空、背景）。"""


def image_to_data_url(path: Path, max_side: int = 1280) -> str:
    """图片转 data URL；有 Pillow 就压到长边 1280 并转 JPEG。"""
    try:
        from PIL import Image
        with Image.open(path) as im:
            im = im.convert("RGB")
            if max(im.size) > max_side:
                im.thumbnail((max_side, max_side), Image.LANCZOS)
            buffer = io.BytesIO()
            im.save(buffer, format="JPEG", quality=88)
        payload = buffer.getvalue()
        mime = "image/jpeg"
    except ImportError:
        payload = path.read_bytes()
        mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return f"data:{mime};base64," + base64.b64encode(payload).decode()


def error_code(resp) -> str:
    """从智谱的错误体里取 code，例如 {"error":{"code":"1305",...}}。"""
    try:
        body = resp.json()
    except ValueError:
        return ""
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            return str(err.get("code") or "")
        return str(body.get("code") or "")
    return ""


def error_message(resp) -> str:
    try:
        body = resp.json()
    except ValueError:
        return resp.text[:120]
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        return str(body["error"].get("message") or "")
    return str(body)[:120]


def ask(url: str, key: str, model: str, prompt: str, data_url: str, timeout: float):
    """发一次请求，返回 (resp, 耗时毫秒)。"""
    import requests
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": data_url}},
            {"type": "text", "text": prompt},
        ]}],
        "temperature": 0.1,   # 智谱要求 (0, 1)，传 0 会被拒
        "stream": False,
    }
    started = time.perf_counter()
    resp = requests.post(url, headers={"Authorization": f"Bearer {key}",
                                       "Content-Type": "application/json"},
                         json=payload, timeout=timeout)
    return resp, int((time.perf_counter() - started) * 1000)


def main() -> int:
    parser = argparse.ArgumentParser(description="验证视觉模型接口")
    parser.add_argument("image", nargs="?", default=str(DEFAULT_IMAGE), help="图片路径")
    parser.add_argument("question", nargs="?", default="这张图里有什么？用两三句话说明。", help="问题")
    parser.add_argument("--model", help="模型名，可用逗号分隔多个做后备，例如 a,b")
    parser.add_argument("--json", action="store_true", help="要求按分页数据字段输出 JSON")
    parser.add_argument("--attempts", type=int, default=2, help="每个模型最多试几次（默认 2）")
    parser.add_argument("--wait", type=float, default=3.0, help="重试的起始等待秒数（默认 3，逐次翻倍）")
    parser.add_argument("--timeout", type=float, default=120.0, help="单次请求超时秒数")
    args = parser.parse_args()

    from configs.settings import load_dotenv

    load_dotenv()
    url = os.environ.get("PBSC_VLM_URL", "").strip()
    key = os.environ.get("PBSC_VLM_API_KEY", "").strip()
    if not url or not key or key in ("xxxx", "your-key-here"):
        print("没有配置好：请在仓库根目录的 .env 里填 PBSC_VLM_URL 和 PBSC_VLM_API_KEY", file=sys.stderr)
        return 2

    image = Path(args.image)
    if not image.is_file():
        print(f"找不到图片：{image}", file=sys.stderr)
        return 2

    env_model = os.environ.get("PBSC_VLM_MODEL", "").strip()
    if args.model:
        models = [m.strip() for m in args.model.split(",") if m.strip()]
    else:
        models = [m for m in [env_model] if m] or []
        models += [m for m in FALLBACK_MODELS if m not in models]

    prompt = JSON_PROMPT if args.json else args.question
    data_url = image_to_data_url(image)
    print(f"POST {url}")
    print(f"image={image.name}  bytes={image.stat().st_size}  候选模型={', '.join(models)}")
    print()

    resp = None
    model_used = ""
    elapsed = 0
    for model in models:
        for attempt in range(1, args.attempts + 1):
            print(f"[{model}] 第 {attempt} 次请求...", end=" ", flush=True)
            try:
                resp, elapsed = ask(url, key, model, prompt, data_url, args.timeout)
            except Exception as exc:                      # 网络层
                print(f"失败：{type(exc).__name__}")
                if attempt < args.attempts:
                    time.sleep(args.wait * attempt)
                continue

            if resp.status_code < 400:
                print(f"OK  {elapsed} ms")
                model_used = model
                break

            code = error_code(resp)
            print(f"HTTP {resp.status_code} code={code or '-'}  {error_message(resp)[:60]}")

            if resp.status_code in FATAL_STATUS:
                print("\nKey 不对或没有权限，先修 .env 里的 PBSC_VLM_API_KEY，换模型没用。", file=sys.stderr)
                return 1
            if resp.status_code in RETRYABLE_STATUS or code in BUSY_CODES:
                if attempt < args.attempts:
                    wait = args.wait * attempt
                    print(f"    免费模型在排队，等 {wait:.0f} 秒重试...")
                    time.sleep(wait)
                continue
            break                                          # 4xx 参数/模型问题，换下一个模型
        if model_used:
            break
        if resp is not None and resp.status_code in FATAL_STATUS:
            return 1
        if model != models[-1]:
            print(f"    {model} 暂时不可用，换下一个候选模型。\n")

    if not model_used:
        print()
        print("所有候选模型都没成功。免费模型在高峰期会长时间排队（1305），", file=sys.stderr)
        print("过几分钟再跑一次；或者先用本地 Qwen2.5-VL 顶上（见 README）。", file=sys.stderr)
        return 1

    body = resp.json()
    try:
        choice = body["choices"][0]["message"]
        text = (choice.get("content") or "").strip()
        reasoning = (choice.get("reasoning_content") or "").strip()
    except (KeyError, IndexError, TypeError):
        print("返回结构不符，原始响应：", file=sys.stderr)
        print(json.dumps(body, ensure_ascii=False)[:800], file=sys.stderr)
        return 1

    usage = body.get("usage") or {}
    print(f"\n用时 {elapsed} ms  model={body.get('model') or model_used}  usage={usage}")
    if reasoning:
        print(f"[思考过程 {len(reasoning)} 字，已省略]")
    if not text and reasoning:
        print("注意：content 为空，正文在 reasoning_content 里。", file=sys.stderr)
        text = reasoning
    print("\n--- 回复 ---")
    print(text[:2000])

    if args.json:
        cleaned = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            print("\n[!] 不是合法 JSON，真实链路需要按 llm_client 的方式加解析和重试", file=sys.stderr)
            return 1
        print("\n--- 解析结果 ---")
        print(f"full_text    : {str(data.get('full_text', ''))[:80]}")
        print(f"scene_summary: {str(data.get('scene_summary', ''))[:80]}")
        objects = data.get("objects") or []
        print(f"objects      : {len(objects)} 个")
        for item in objects[:8]:
            if isinstance(item, dict):
                print(f"  - {item.get('name')} {item.get('bbox')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
