# -*- coding: utf-8 -*-
"""给本地 llama.cpp 推理服务做一次大 prompt 预热。

Vulkan 后端在第一次处理较大的 prompt 时需要编译/缓存管线，冷启动首问可能 10–20 秒；
预热一次后，正常问答的首问一般 1–3 秒。启动 `scripts/start_llm.ps1` 之后跑一遍本脚本即可。

用法（项目根目录）：
    .venv\\Scripts\\python scripts\\warmup_llm.py
    .venv\\Scripts\\python scripts\\warmup_llm.py --url http://127.0.0.1:8080
"""
from __future__ import annotations

import argparse
import time

import requests

SYSTEM = ("你是儿童绘本伴读助手。只根据给出的书中内容回答，找不到依据就说“这本书里没有讲到哦。”"
          "回答要短，给 3-8 岁孩子听。只输出 JSON。")
PASSAGE = ("第2页 原文：朵朵走到小河边，发现月亮掉进了水里。乌龟爷爷慢慢地说：“那是月亮的影子。”"
           "旁白：朵朵走呀走，来到了小河边。咦，月亮怎么掉进水里啦？乌龟爷爷慢慢地说：别着急，那是月亮的影子。"
           "画面：小河边，河水里映着月亮的倒影，朵朵站在左岸看着水面，乌龟爷爷在右岸。")


def warmup(base_url: str, repeats: int = 30, rounds: int = 2) -> int:
    endpoint = base_url.rstrip("/") + "/v1/chat/completions"
    try:
        health = requests.get(base_url.rstrip("/") + "/health", timeout=5).json()
    except requests.RequestException as exc:
        print(f"连不上推理服务 {base_url}：{exc}")
        print("请先启动：powershell -File scripts\\start_llm.ps1")
        return 1
    if health.get("status") != "ok":
        print(f"推理服务状态异常：{health}")
        return 1

    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": (PASSAGE * repeats) + "\n问题：月亮掉到哪里去了？"}]
    for index in range(1, rounds + 1):
        started = time.perf_counter()
        try:
            resp = requests.post(endpoint, json={"model": "qwen3-4b-instruct-2507", "messages": messages,
                                                 "temperature": 0.0, "max_tokens": 8},
                                 timeout=300)
            resp.raise_for_status()
        except requests.RequestException as exc:
            print(f"预热第 {index} 轮失败：{exc}")
            return 1
        elapsed = int((time.perf_counter() - started) * 1000)
        usage = resp.json().get("usage") or {}
        print(f"预热第 {index} 轮：{elapsed} ms，prompt_tokens={usage.get('prompt_tokens')}")
    print("预热完成。实际问答延迟请以同配置下的测试为准。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="预热本地 Qwen3 推理服务")
    parser.add_argument("--url", default="http://127.0.0.1:8080", help="llama-server 地址")
    parser.add_argument("--repeats", type=int, default=30, help="上下文重复段数，默认 30（约 900 字）")
    parser.add_argument("--rounds", type=int, default=2, help="预热轮数，默认 2")
    args = parser.parse_args()
    return warmup(args.url, args.repeats, args.rounds)


if __name__ == "__main__":
    raise SystemExit(main())
