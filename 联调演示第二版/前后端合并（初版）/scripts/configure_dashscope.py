# -*- coding: utf-8 -*-
"""安全地写入阿里云百炼配置到本地 .env（Key 输入不回显、不进入命令历史）。

用法（在项目根目录，自己终端里运行）：
    .venv\\Scripts\\python scripts\\configure_dashscope.py
    .venv\\Scripts\\python scripts\\configure_dashscope.py --workspace 你的WorkspaceID
    .venv\\Scripts\\python scripts\\configure_dashscope.py --base-url https://ws-xxxx.cn-beijing.maas.aliyuncs.com/api/v1

脚本只写 `.env`（已被 .gitignore 忽略），不会把 Key 打到屏幕或聊天里。
写完后再运行 scripts\\check_dashscope.py 验证认证。
"""
from __future__ import annotations

import argparse
import getpass
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
KEY = "DASHSCOPE_API_KEY"


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


def existing_value(lines: list[str], name: str) -> str:
    pattern = re.compile(rf"^\s*{re.escape(name)}\s*=(.*)$")
    for line in lines:
        match = pattern.match(line)
        if match:
            return match.group(1).strip()
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description="安全配置阿里云百炼图生视频")
    parser.add_argument("--workspace", default=None,
                        help="百炼业务空间 ID；调用地址形如 ws-xxxx.cn-beijing.maas.aliyuncs.com")
    parser.add_argument("--base-url", default=None,
                        help="可选的 workspace 专属地址，填到 /api/v1 为止")
    parser.add_argument("--model", default="wan3.0-video-prime", help="默认 wan3.0-video-prime")
    parser.add_argument("--resolution", default="1080P", choices=["480P", "720P", "1080P"])
    parser.add_argument("--width", type=int, default=1920, help="最终合成视频宽度")
    parser.add_argument("--height", type=int, default=1080, help="最终合成视频高度")
    args = parser.parse_args()

    lines = ENV_PATH.read_text(encoding="utf-8").splitlines(keepends=True) if ENV_PATH.is_file() else []
    old_key = existing_value(lines, KEY)
    prompt = "百炼 API Key（输入不显示；直接回车保留现有值）："
    key = getpass.getpass(prompt).strip()
    if not key:
        key = old_key
    if not key or key == "xxxx":
        print("没有可用的 API Key，已取消。")
        return 1
    if not key.startswith("sk-"):
        print("提示：百炼 Key 通常以 sk- 开头，请确认没有复制错。")

    lines = upsert(lines, "PBSC_I2V_PROVIDER", "dashscope")
    lines = upsert(lines, KEY, key)
    lines = upsert(lines, "PBSC_DASHSCOPE_MODEL", args.model)
    lines = upsert(lines, "PBSC_DASHSCOPE_RESOLUTION", args.resolution)
    lines = upsert(lines, "PBSC_VIDEO_WIDTH", str(args.width))
    lines = upsert(lines, "PBSC_VIDEO_HEIGHT", str(args.height))
    if args.workspace:
        lines = upsert(lines, "DASHSCOPE_WORKSPACE_ID", args.workspace)
    if args.base_url:
        lines = upsert(lines, "PBSC_DASHSCOPE_BASE_URL", args.base_url.rstrip("/"))

    ENV_PATH.write_text("".join(lines), encoding="utf-8")
    print(f"已写入 {ENV_PATH}（Key 未回显）。")
    print("下一步：.venv\\Scripts\\python scripts\\check_dashscope.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
