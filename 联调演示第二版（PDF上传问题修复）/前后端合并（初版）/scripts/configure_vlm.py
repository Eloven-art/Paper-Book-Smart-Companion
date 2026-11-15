# -*- coding: utf-8 -*-
"""安全写入 VLM 配置到本地 .env（API Key 隐藏输入，不回显、不进命令历史）。

用法（在项目根目录，自己的终端里运行）：
    .venv\\Scripts\\python scripts\\configure_vlm.py --url http://127.0.0.1:9988/v1/chat/completions --model vlm_query2func_model

没有鉴权的本地服务：提示 Key 时直接回车即可。
有鉴权的远程服务：在提示里粘贴 Key，输入不会显示。
"""
from __future__ import annotations

import argparse
import getpass
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"


def upsert(lines: list[str], name: str, value: str) -> list[str]:
    pattern = re.compile(rf"^\s*{re.escape(name)}\s*=")
    output, replaced = [], False
    for line in lines:
        if pattern.match(line):
            if not replaced:
                output.append(f"{name}={value}\n")
                replaced = True
            continue
        output.append(line)
    if not replaced:
        output.append(f"{name}={value}\n")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="安全配置 VLM 接口")
    parser.add_argument("--url", default=None, help="OpenAI 兼容 /chat/completions 的完整地址")
    parser.add_argument("--model", default="vlm_query2func_model", help="模型名")
    parser.add_argument("--protocol", default="openai_chat", choices=["openai_chat", "custom_json"])
    parser.add_argument("--bbox-format", default="xyxy_1000",
                        choices=["xyxy", "xywh", "xyxy_1000", "xyxy_normalized"],
                        help="参考代码按 0-1000 换算，默认 xyxy_1000；如果真实 JSON 是像素就改 xyxy")
    args = parser.parse_args()

    url = (args.url or input("VLM URL（例如 http://127.0.0.1:9988/v1/chat/completions）：")).strip()
    if not url.startswith(("http://", "https://")):
        print("URL 必须以 http:// 或 https:// 开头。")
        return 1
    if url.rstrip("/").endswith("/api/analyze-image"):
        print("提示：这是 Flask 包装接口；当前适配层需要 OpenAI 兼容的 /v1/chat/completions。")
        print("如果只有这个地址，请告诉我，我再加对应协议。")
        return 1

    key = getpass.getpass("VLM API Key（没有鉴权直接回车；输入不显示）：").strip()

    lines = ENV_PATH.read_text(encoding="utf-8").splitlines(keepends=True) if ENV_PATH.is_file() else []
    lines = upsert(lines, "IMAGE_ANALYSIS_URL", url)
    lines = upsert(lines, "IMAGE_MODEL", args.model)
    lines = upsert(lines, "PBSC_VLM_PROTOCOL", args.protocol)
    lines = upsert(lines, "PBSC_VLM_BBOX_FORMAT", args.bbox_format)
    if key:
        lines = upsert(lines, "IMAGE_API_KEY", key)
    ENV_PATH.write_text("".join(lines), encoding="utf-8")
    print(f"已写入 {ENV_PATH}")
    print("下一步：.venv\\Scripts\\python scripts\\generate_book.py --book demo_book --steps vlm --page 1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
