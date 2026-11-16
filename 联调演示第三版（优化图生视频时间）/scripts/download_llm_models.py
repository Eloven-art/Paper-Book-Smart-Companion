# -*- coding: utf-8 -*-
"""下载 / 校验本地问答模型 Qwen3-4B-Instruct-2507（GGUF Q4_K_M）。

用法（在项目根目录）：
    .venv\\Scripts\\python scripts\\download_llm_models.py
    .venv\\Scripts\\python scripts\\download_llm_models.py --model-dir D:\\models\\qwen3

模型来自 ModelScope / Hugging Face 的 `unsloth/Qwen3-4B-Instruct-2507-GGUF`（Apache-2.0），
默认放在 `data/models/qwen3/`（可用 PBSC_LLM_MODEL_DIR 覆盖），约 2.33 GB。模型目录不入库。
默认先走 ModelScope，失败再试 hf-mirror.com、huggingface.co；支持断点续传（.part 文件）。
运行时由 scripts/start_llm.ps1 启动 third_party/llama.cpp 的 llama-server。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from scripts.model_download import download, valid

FILENAME = "Qwen3-4B-Instruct-2507-Q4_K_M.gguf"
EXPECTED_SIZE = 2497281120
EXPECTED_SHA256 = "3605803b982cb64aead44f6c1b2ae36e3acdb41d8e46c8a94c6533bc4c67e597"
SOURCES = (
    f"https://modelscope.cn/models/unsloth/Qwen3-4B-Instruct-2507-GGUF/resolve/master/{FILENAME}",
    f"https://hf-mirror.com/unsloth/Qwen3-4B-Instruct-2507-GGUF/resolve/main/{FILENAME}",
    f"https://huggingface.co/unsloth/Qwen3-4B-Instruct-2507-GGUF/resolve/main/{FILENAME}",
)


def main() -> int:
    parser = argparse.ArgumentParser(description="下载本地问答模型 Qwen3-4B-Instruct-2507 GGUF")
    parser.add_argument("--model-dir", default=None,
                        help="模型目录，默认 PBSC_LLM_MODEL_DIR 或 data/models/qwen3")
    parser.add_argument("--lite", action="store_true", help="download Qwen3 0.6B for CPU")
    args = parser.parse_args()

    from configs.settings import get_settings
    settings = get_settings()
    default_dir = Path(os.environ.get("PBSC_LLM_MODEL_DIR") or settings.data_dir / "models" / "qwen3")
    model_dir = Path(args.model_dir or default_dir).expanduser().resolve()
    model_dir.mkdir(parents=True, exist_ok=True)
    print(f"模型目录：{model_dir}")

    target = model_dir / FILENAME
    sources, size, digest = SOURCES, EXPECTED_SIZE, EXPECTED_SHA256
    if args.lite:
        target = model_dir / "Qwen3-0.6B-Q8_0.gguf"
        path = "/Qwen/Qwen3-0.6B-GGUF/resolve/1eaf4d9/" + target.name
        sources = tuple(host + path for host in ("https://huggingface.co","https://hf-mirror.com"))
        size, digest = None, "9465e63a22add5354d9bb4b99e90117043c7124007664907259bd16d043bb031"
    if valid(target,size,digest):
        print(f"  已存在且校验通过：{target.name}")
        return 0

    errors = []
    for url in sources:
        print(f"尝试：{url.split('/')[2]}")
        try:
            download(url, target, size, digest)
            print(f"完成：{target}")
            return 0
        except Exception as exc:       # noqa: BLE001 - 换下一个源继续
            errors.append(f"{url.split('/')[2]}: {type(exc).__name__}: {exc}")
            print(f"  失败：{type(exc).__name__}: {exc}")
    print("所有下载源都失败：")
    for error in errors:
        print("  - " + error)
    print("可手动下载后放到：" + str(target))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
