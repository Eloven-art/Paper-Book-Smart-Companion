#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""双击「检查VLM.bat」时真正干活的部分：引导填 key、装依赖、调用一次视觉模型。

之所以把中文提示放在 Python 里而不是 .bat 里：.bat 用 UTF-8 存中文时，cmd 会按 GBK
解析，中文字节会把换行吃掉，导致“不是内部或外部命令”的报错。Python 的输出编码可控。

参数（一般不用手动传，双击时全走默认）：
    --skip-env      不弹记事本
    --skip-install  跳过 pip 安装
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"
CHECK = ROOT / "scripts" / "check_vlm.py"
MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def line(char: str = "-", width: int = 60) -> None:
    print(char * width)


def read_key_state() -> str:
    """返回 raw（已填）/ placeholder（还是占位符）/ missing（没这一行）。"""
    if not ENV_FILE.is_file():
        return "missing"
    for raw in ENV_FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
        text = raw.strip()
        if text.startswith("PBSC_VLM_API_KEY"):
            value = text.split("=", 1)[1].strip() if "=" in text else ""
            if not value or value in ("xxxx", "这里换成你的key", "your-key-here"):
                return "placeholder"
            return "raw"
    return "missing"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-env", action="store_true")
    parser.add_argument("--skip-install", action="store_true")
    args = parser.parse_args()

    line("=")
    print("  纸书智伴 · 视觉模型检查（glm-4.6v-flash）")
    line("=")
    print(f"  Python : {sys.version.split()[0]}  ({sys.executable})")
    print(f"  项目目录: {ROOT}")
    print(f"  配置文件: {ENV_FILE}")
    print()

    state = read_key_state()
    if state == "raw":
        print("  [1/3] 配置文件里已经有 Key 了，跳过这一步。")
    else:
        if not ENV_FILE.is_file():
            reason = "项目里还没有 .env 文件"
        elif state == "placeholder":
            reason = "Key 还是占位符"
        else:
            reason = ".env 里还没有 PBSC_VLM_API_KEY 这一行"
        print(f"  [1/3] 现在要填 Key（{reason}）。")
        print()
        print("        接下来会弹出一个记事本窗口，在里面找到这一行：")
        print()
        print("            PBSC_VLM_API_KEY=这里换成你的key")
        print()
        print("        把等号后面的内容换成你在 bigmodel.cn 复制的 Key，")
        print("        按 Ctrl+S 保存，然后关掉记事本。")
        print("        没有这一行的话，就把下面三行加到文件最末尾：")
        print()
        print("            PBSC_VLM_URL=https://open.bigmodel.cn/api/paas/v4/chat/completions")
        print("            PBSC_VLM_API_KEY=你的key")
        print("            PBSC_VLM_MODEL=glm-4.6v-flash")
        print()
        if args.skip_env:
            print("  （--skip-env：不弹记事本）")
        else:
            input("        准备好后按回车，打开记事本...")
            try:
                subprocess.call(["notepad.exe", str(ENV_FILE)])
            except OSError:
                print("  打不开记事本，请手动用记事本打开 .env")
            if read_key_state() != "raw":
                print()
                print("  [!] 看起来 Key 还没填好（仍然是占位符或没这一行）。")
                print("      继续跑也行，脚本会再提示一次。")
    print()

    print("  [2/3] 检查依赖（第一次要等十几秒，请不要关窗口）...")
    if args.skip_install:
        print("        （--skip-install：跳过）")
    else:
        for index in (MIRROR, None):
            command = [sys.executable, "-m", "pip", "install", "--user", "-q",
                       "--disable-pip-version-check"]
            if index:
                command += ["-i", index]
            command += ["requests", "pillow"]
            code = subprocess.call(command)
            if code == 0:
                break
        else:
            print()
            print("  [X] 依赖安装失败。把上面的报错截图发出来。")
            print("      常见原因：没联网，或者公司的网络拦截了 pip。")
            return 1
        print("        依赖就绪。")
    print()

    print("  [3/3] 调用视觉模型（用示例绘本第 1 页）...")
    line()
    print()
    sys.stdout.flush()
    code = subprocess.call([sys.executable, str(CHECK), "--json"])
    print()
    line("=")
    if code == 0:
        print("  成功：上面“解析结果”里有 full_text 和带方括号的坐标。")
        print("  把这一段复制给队友，就可以接下一步了。")
    else:
        print("  没有成功。把上面最后 10 行发出来，对照报错表：")
        print("    401 / 令牌无效  → Key 复制错或有空格")
        print("    1302           → 免费模型并发超限，等几秒重跑")
        print("    1305           → 平台繁忙，等几分钟")
        print("    没有配置好      → .env 没保存，或变量名拼错")
    line("=")
    # 始终返回 0：.bat 里用 `where python && ... || py -3.11 ...` 做回退，
    # 这里返回非 0 会让它误判成“没有 python”而再跑一遍。
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
