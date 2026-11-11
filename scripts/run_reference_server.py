#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""在本机启动参考后端（参考代码/story_tts_server.py），不修改参考代码。

解决两个本机直接运行时会遇到的问题（详见 docs/A1-参考后端接口清单.md）：
1. 前端 tts_v2.html 的请求都带 /tts-story-book 前缀，而后端的 /api 路由没有这个前缀。
   这里把同一个应用同时挂在 / 和 /tts-story-book 下，两种地址都能访问。
2. 参考代码里有带表情符号的 print，中文 Windows 上把输出重定向到文件时会因 GBK 编码报错。
   这里启动前把标准输出切到 UTF-8。

用法（在仓库根目录）：
    python scripts/run_reference_server.py            # 默认 127.0.0.1:5000
    python scripts/run_reference_server.py --port 5001 --host 0.0.0.0
然后浏览器打开 http://127.0.0.1:5000/tts-story-book/tts_v2.html

外部服务地址和密钥通过环境变量配置（变量名见 参考代码/config.py 或仓库根目录 .env.example）。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REF_DIR = ROOT / "参考代码"
PREFIX = "/tts-story-book"


def build_app():
    """返回同时支持带前缀和不带前缀两种地址的 WSGI 应用。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    # 参考后端把生成结果写到 TARGET_DIR；默认放到 data/reference_runtime，避免写进参考代码目录
    os.environ.setdefault("TARGET_DIR", str(ROOT / "data" / "reference_runtime").replace("\\", "/"))
    target = Path(os.environ["TARGET_DIR"])
    target.mkdir(parents=True, exist_ok=True)
    for name in ("bookshelf.json",):
        if not (target / name).exists() and (REF_DIR / name).exists():
            (target / name).write_bytes((REF_DIR / name).read_bytes())
    if not (target / "history_res").exists() and (REF_DIR / "history_res").exists():
        import shutil
        shutil.copytree(REF_DIR / "history_res", target / "history_res")

    sys.path.insert(0, str(REF_DIR))
    os.chdir(target)  # 参考后端有少量相对路径写入
    import story_tts_server as ref
    from werkzeug.middleware.dispatcher import DispatcherMiddleware

    return DispatcherMiddleware(ref.app.wsgi_app, {PREFIX: ref.app.wsgi_app})


def main() -> int:
    parser = argparse.ArgumentParser(description="启动参考后端（带前缀兼容）")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址，默认只允许本机访问")
    parser.add_argument("--port", type=int, default=5000)
    args = parser.parse_args()

    app = build_app()
    from werkzeug.serving import run_simple

    print(f"参考后端已启动：http://{args.host}:{args.port}{PREFIX}/tts_v2.html")
    run_simple(args.host, args.port, app, threaded=True, use_reloader=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
