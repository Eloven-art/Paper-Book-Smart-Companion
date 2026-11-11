# -*- coding: utf-8 -*-
"""查找可显示中文的字体，用于视频字幕和示例绘本的页面文字。

优先使用开源许可的字体（Noto Sans SC / 思源黑体，SIL OFL），找不到再退到系统自带字体。
最终交付的视频用了哪种字体会写进运行记录，便于在素材来源清单里披露。
可用环境变量 PBSC_SUBTITLE_FONT 指定字体文件的绝对路径。
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Optional

# (文件名或路径片段, 显示名, 是否开源许可)
_CANDIDATES = [
    ("Noto Sans SC Medium (TrueType).otf", "Noto Sans SC Medium", True),
    ("Noto Sans SC (TrueType).otf", "Noto Sans SC", True),
    ("NotoSansSC-Medium.otf", "Noto Sans SC Medium", True),
    ("NotoSansSC-Regular.otf", "Noto Sans SC", True),
    ("NotoSansSC-Regular.ttf", "Noto Sans SC", True),
    ("NotoSansCJK-Regular.ttc", "Noto Sans CJK", True),
    ("NotoSansCJKsc-Regular.otf", "Noto Sans CJK SC", True),
    ("SourceHanSansSC-Regular.otf", "思源黑体", True),
    ("SourceHanSansCN-Regular.otf", "思源黑体", True),
    ("wqy-microhei.ttc", "文泉驿微米黑", True),
    ("wqy-zenhei.ttc", "文泉驿正黑", True),
    ("msyh.ttc", "微软雅黑（系统字体）", False),
    ("simhei.ttf", "黑体（系统字体）", False),
    ("PingFang.ttc", "苹方（系统字体）", False),
    ("STHeiti Medium.ttc", "华文黑体（系统字体）", False),
]

_FONT_DIRS = [
    Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts",
    Path.home() / "AppData/Local/Microsoft/Windows/Fonts",
    Path("/usr/share/fonts"),
    Path("/usr/local/share/fonts"),
    Path.home() / ".fonts",
    Path.home() / ".local/share/fonts",
    Path("/System/Library/Fonts"),
    Path("/Library/Fonts"),
]


@lru_cache(maxsize=8)
def find_cjk_font(preferred: Optional[str] = None) -> Optional[dict]:
    """返回 {"path", "name", "open_license"}；一个都找不到时返回 None（调用方应跳过字幕并记录）。"""
    if preferred:
        p = Path(preferred)
        if p.is_file():
            return {"path": str(p), "name": p.stem, "open_license": None}
    existing_dirs = [d for d in _FONT_DIRS if d.is_dir()]
    for filename, name, open_license in _CANDIDATES:
        for directory in existing_dirs:
            direct = directory / filename
            if direct.is_file():
                return {"path": str(direct), "name": name, "open_license": open_license}
            if directory.as_posix().startswith(("/usr", "/Library", "/System")) or directory.name in (".fonts", "fonts"):
                for found in directory.rglob(filename):
                    return {"path": str(found), "name": name, "open_license": open_license}
    return None
