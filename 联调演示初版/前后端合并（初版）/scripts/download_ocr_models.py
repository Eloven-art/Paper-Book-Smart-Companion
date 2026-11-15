# -*- coding: utf-8 -*-
"""预下载 / 校验 RapidOCR 模型。

用法（在项目根目录）：
    .venv\\Scripts\\python scripts\\download_ocr_models.py
    .venv\\Scripts\\python scripts\\download_ocr_models.py --model-dir D:\\models\\rapidocr

模型默认放在 `data/models/rapidocr/`（可用 PBSC_OCR_MODEL_DIR 覆盖）。
推理引擎是 onnxruntime，模型为 PP-OCRv6 det/rec small + PP-OCRv4 cls mobile，约 31 MB。
已有模型会被校验后复用；模型目录不入库，新环境请先运行本脚本或让首次 OCR 自动下载。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="下载 RapidOCR 本地推理模型")
    parser.add_argument("--model-dir", default=None, help="模型目录，默认取 PBSC_OCR_MODEL_DIR")
    args = parser.parse_args()

    from configs.settings import get_settings
    from agents.ocr_agent import VENDOR_ROOT

    if not (VENDOR_ROOT / "rapidocr" / "__init__.py").is_file():
        print(f"找不到 RapidOCR 源码：{VENDOR_ROOT / 'rapidocr'}", file=sys.stderr)
        return 1

    settings = get_settings()
    model_dir = Path(args.model_dir or settings.ocr_model_dir).expanduser().resolve()
    model_dir.mkdir(parents=True, exist_ok=True)
    print(f"模型目录：{model_dir}")

    try:
        import numpy as np
        from rapidocr import RapidOCR
    except Exception as exc:      # noqa: BLE001
        print(f"导入 RapidOCR 失败：{exc}\n请先执行 pip install -r requirements.txt", file=sys.stderr)
        return 1

    engine = RapidOCR(params={"Global.model_root_dir": str(model_dir), "Global.log_level": "info"})
    # 实例化是惰性的；用一张白图触发三个模型下载/加载。
    engine(np.full((64, 256, 3), 255, dtype=np.uint8))

    files = sorted(p.name for p in model_dir.glob("*.onnx"))
    print("已就绪的模型：")
    for name in files:
        print(f"  - {name}（{(model_dir / name).stat().st_size / 1024 / 1024:.1f} MB）")
    if len(files) < 3:
        print("模型不完整，请检查网络或 PBSC_OCR_MODEL_DIR。", file=sys.stderr)
        return 1
    print("OCR 模型准备完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
