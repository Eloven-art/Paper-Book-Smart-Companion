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
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FILENAME = "Qwen3-4B-Instruct-2507-Q4_K_M.gguf"
EXPECTED_SIZE = 2497281120
SOURCES = (
    f"https://modelscope.cn/models/unsloth/Qwen3-4B-Instruct-2507-GGUF/resolve/master/{FILENAME}",
    f"https://hf-mirror.com/unsloth/Qwen3-4B-Instruct-2507-GGUF/resolve/main/{FILENAME}",
    f"https://huggingface.co/unsloth/Qwen3-4B-Instruct-2507-GGUF/resolve/main/{FILENAME}",
)


def _download(url: str, dest: Path) -> None:
    tmp = dest.with_suffix(dest.suffix + ".part")
    done = tmp.stat().st_size if tmp.is_file() else 0
    headers = {"Range": f"bytes={done}-"} if done else {}
    mode = "ab" if done else "wb"
    with requests.get(url, stream=True, timeout=(20, 120), headers=headers) as response:
        if done and response.status_code == 200:
            done, mode = 0, "wb"          # 服务端不支持 Range，重新下载
        response.raise_for_status()
        total = int(response.headers.get("content-length") or 0) + done
        last = time.time()
        with open(tmp, mode) as fh:
            for chunk in response.iter_content(4 * 1024 * 1024):
                if not chunk:
                    continue
                fh.write(chunk)
                done += len(chunk)
                if time.time() - last >= 2:
                    last = time.time()
                    print(f"\r  {dest.name}: {done / 1024 / 1024:.1f} / {total / 1024 / 1024:.1f} MB",
                          end="", flush=True)
    print()
    actual = tmp.stat().st_size
    if actual != EXPECTED_SIZE:
        raise RuntimeError(f"{dest.name} 大小不符：期望 {EXPECTED_SIZE}，实际 {actual}")
    tmp.replace(dest)


def main() -> int:
    parser = argparse.ArgumentParser(description="下载本地问答模型 Qwen3-4B-Instruct-2507 GGUF")
    parser.add_argument("--model-dir", default=None,
                        help="模型目录，默认 PBSC_LLM_MODEL_DIR 或 data/models/qwen3")
    args = parser.parse_args()

    from configs.settings import get_settings
    settings = get_settings()
    default_dir = Path(os.environ.get("PBSC_LLM_MODEL_DIR") or settings.data_dir / "models" / "qwen3")
    model_dir = Path(args.model_dir or default_dir).expanduser().resolve()
    model_dir.mkdir(parents=True, exist_ok=True)
    print(f"模型目录：{model_dir}")

    target = model_dir / FILENAME
    if target.is_file() and target.stat().st_size == EXPECTED_SIZE:
        print(f"  已存在且大小正确：{target.name}")
        return 0

    errors = []
    for url in SOURCES:
        print(f"尝试：{url.split('/')[2]}")
        try:
            _download(url, target)
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
