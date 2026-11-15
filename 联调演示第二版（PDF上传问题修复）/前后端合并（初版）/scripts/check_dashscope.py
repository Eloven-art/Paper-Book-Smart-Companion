# -*- coding: utf-8 -*-
"""检查阿里云百炼图生视频配置和 API Key 认证（不生成视频、不产生生成费用）。

用法（在项目根目录）：
    .venv\\Scripts\\python scripts\\check_dashscope.py

只读取 .env / 环境变量，不打印密钥本身；调用的是任务列表接口，用来确认
Key、区域和 Workspace 是否可用。真正生成视频在网页的“开始生成”或
`python scripts/generate_book.py --book demo_book --steps video` 中触发。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from configs.settings import get_settings  # noqa: E402
from agents.i2v_client import DashScopeI2V  # noqa: E402


def main() -> int:
    settings = get_settings()
    print(f"provider：{settings.i2v_provider}")
    print(f"model：   {settings.dashscope_model}")
    print(f"region：  {settings.dashscope_region}")
    print(f"resolution：{settings.dashscope_resolution}")
    print(f"API Key： {'已配置' if settings.dashscope_api_key else '未配置'}")
    print(f"Workspace：{'已配置' if settings.dashscope_workspace else '未配置（使用默认）'}")
    print(f"Base URL： {'已配置' if settings.dashscope_base_url else '默认（按 region/workspace 推导）'}")
    if settings.i2v_provider != "dashscope":
        print("当前 PBSC_I2V_PROVIDER 不是 dashscope。请在 .env 中设置后重试。", file=sys.stderr)
        return 1
    if not settings.dashscope_api_key:
        print("缺少 DASHSCOPE_API_KEY。请在 .env 中填写，或设置同名环境变量。", file=sys.stderr)
        return 1
    engine = DashScopeI2V(
        settings.dashscope_api_key, settings.dashscope_model,
        workspace=settings.dashscope_workspace, region=settings.dashscope_region,
        base_url=settings.dashscope_base_url,
        media_protocol=settings.dashscope_media_protocol, resolution=settings.dashscope_resolution,
        ratio=settings.dashscope_ratio, prompt_extend=settings.dashscope_prompt_extend,
        watermark=settings.dashscope_watermark, timeout=settings.dashscope_timeout)
    try:
        client = engine._get_client()  # noqa: SLF001 - 只做认证检查，不提交生成任务
        jobs = client.videos.jobs.list(page_size=1)
        print(f"认证成功。最近任务数：{getattr(jobs, 'total', '未知')}")
    except Exception as exc:  # noqa: BLE001
        print(f"认证或查询失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
