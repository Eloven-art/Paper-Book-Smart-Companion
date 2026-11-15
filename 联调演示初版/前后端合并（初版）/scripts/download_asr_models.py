# -*- coding: utf-8 -*-
"""下载 / 校验 SenseVoiceSmall 的 ONNX 量化模型。

用法（在项目根目录）：
    .venv\\Scripts\\python scripts\\download_asr_models.py
    .venv\\Scripts\\python scripts\\download_asr_models.py --model-dir D:\\models\\sensevoice

模型来自 Hugging Face 的 `haixuantao/SenseVoiceSmall-onnx`（iic/SenseVoiceSmall 的 int8 量化导出，
Apache-2.0），默认放在 `data/models/sensevoice/`（可用 PBSC_ASR_MODEL_DIR 覆盖），约 230 MB。
默认先走 hf-mirror.com，失败再试 huggingface.co。模型目录不入库，新环境请先运行本脚本。
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

REPO = "haixuantao/SenseVoiceSmall-onnx"
MIRRORS = ("https://hf-mirror.com", "https://huggingface.co")
FILES = {
    "model_quant.onnx": 241216270,
    "config.yaml": 1855,
    "am.mvn": 11203,
    "chn_jpn_yue_eng_ko_spectok.bpe.model": 377341,
    "tokens.json": 352064,
    "configuration.json": 56,
    "README.md": 4810,
}
RENAMES = {"README.md": "MODEL_CARD.md"}


def _download(url: str, dest: Path, expected: int) -> None:
    tmp = dest.with_suffix(dest.suffix + ".part")
    with requests.get(url, stream=True, timeout=(20, 60)) as response:
        response.raise_for_status()
        total = int(response.headers.get("content-length") or 0) or expected
        done, last = 0, time.time()
        with open(tmp, "wb") as fh:
            for chunk in response.iter_content(1024 * 1024):
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
    if expected and actual != expected:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"{dest.name} 大小不符：期望 {expected}，实际 {actual}")
    tmp.replace(dest)


def main() -> int:
    parser = argparse.ArgumentParser(description="下载 SenseVoiceSmall ONNX 量化模型")
    parser.add_argument("--model-dir", default=None, help="模型目录，默认取 PBSC_ASR_MODEL_DIR")
    args = parser.parse_args()

    from configs.settings import get_settings
    model_dir = Path(args.model_dir or get_settings().asr_model_dir).expanduser().resolve()
    model_dir.mkdir(parents=True, exist_ok=True)
    print(f"模型目录：{model_dir}")

    for name, expected in FILES.items():
        target = model_dir / RENAMES.get(name, name)
        if target.is_file() and target.stat().st_size == expected:
            print(f"  已存在：{target.name}")
            continue
        errors = []
        for mirror in MIRRORS:
            url = f"{mirror}/{REPO}/resolve/main/{name}"
            try:
                print(f"下载 {name}（{expected / 1024 / 1024:.1f} MB）<- {mirror}")
                _download(url, target, expected)
                break
            except Exception as exc:       # noqa: BLE001 - 换镜像重试
                errors.append(f"{mirror}: {exc}")
                print(f"  失败：{exc}")
        else:
            print(f"下载 {name} 失败：\n  " + "\n  ".join(errors), file=sys.stderr)
            return 1

    missing = [name for name in FILES if not (model_dir / RENAMES.get(name, name)).is_file()]
    if missing:
        print("模型不完整：" + "、".join(missing), file=sys.stderr)
        return 1
    print("SenseVoice 模型准备完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
