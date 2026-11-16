# -*- coding: utf-8 -*-
"""启动服务：python -m server [--host 0.0.0.0] [--port 8000]"""
from __future__ import annotations

import argparse
import logging
import sys

from configs.settings import get_settings


def main() -> int:
    for stream in (sys.stdout, sys.stderr):   # 中文 Windows 上重定向输出时默认是 GBK，统一成 UTF-8
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    settings = get_settings()
    parser = argparse.ArgumentParser(description="纸书智伴后端服务")
    parser.add_argument("--host", default=settings.host,
                        help="监听地址。默认 127.0.0.1 只允许本机；平板联调时用 0.0.0.0")
    parser.add_argument("--port", type=int, default=settings.port)
    parser.add_argument("--open-browser", metavar="PATH", help="服务就绪后打开本机浏览器")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if settings.debug else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    from .app import create_app
    app = create_app(settings)

    services = settings.public_summary()["services"]
    print(f"纸书智伴后端已启动：http://{args.host}:{args.port}", flush=True)
    print(f"  真实接口 /api/v1/health    假接口 /mock/api/v1/health")
    for name, info in services.items():
        print(f"  外部服务 {name}: {'已配置 ' + str(info['model'] or '') if info['configured'] else '未配置（走降级方案）'}")

    from werkzeug.serving import run_simple
    if args.open_browser:
        import threading
        import webbrowser

        from werkzeug.serving import make_server

        live = make_server(args.host, args.port, app, threaded=True)
        browser_host = {"0.0.0.0": "127.0.0.1", "::": "::1"}.get(args.host, args.host)
        if ":" in browser_host:
            browser_host = f"[{browser_host}]"
        url = f"http://{browser_host}:{live.server_port}/{args.open_browser.lstrip('/')}"
        threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()
        live.serve_forever()
        return 0
    run_simple(args.host, args.port, app, threaded=True, use_reloader=False, use_debugger=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
