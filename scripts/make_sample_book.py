#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成“模拟绘本”示例数据：data/samples/demo_book/。

为什么需要它：官方绘本到手前，赛题禁止使用购买或下载的绘本。这里的三页绘本由本脚本
用几何图形画出来，故事文字为队伍原创，没有任何第三方素材，可以放心入库和演示。

用途：
1. 假接口（/mock/api/v1/...）返回的固定示例数据就来自这里；
2. 在它上面试跑真实的旁白与视频生成链路（python scripts/generate_book.py --book demo_book）。

因为物体是脚本画的，每个物体的坐标框是精确已知的，适合前端调试点击识物、D 搭触控测试脚本。

用法（在仓库根目录）：
    python scripts/make_sample_book.py            # 只生成页面图片和 book.json
    python scripts/make_sample_book.py --video    # 另外用本地降级方案合成示例视频并写入关键帧
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from agents.fonts import find_cjk_font  # noqa: E402

W, H = 1280, 720
BOOK_ID = "demo_book"
OUT = ROOT / "data" / "samples" / BOOK_ID
SCHEMA_VERSION = "0.1-draft"

CHARACTERS = [
    {"character_id": "c1", "name": "朵朵", "aliases": ["小兔子朵朵", "小兔子"],
     "appearance": "白色小兔子，长耳朵内侧粉色，圆脸，脖子上围着蓝色围巾", "personality": "好奇、爱提问"},
    {"character_id": "c2", "name": "乌龟爷爷", "aliases": ["乌龟"],
     "appearance": "绿色的老乌龟，深绿色带格纹的龟壳，动作缓慢", "personality": "温和、有耐心"},
]

PAGES_TEXT = [
    {
        "lines": ["夜晚的草地上，小兔子朵朵抬头看见了圆圆的月亮。", "“月亮住在哪里呢？”朵朵想。"],
        "narration": "夜晚，草地上静悄悄的。小兔子朵朵抬起头，看见了一个圆圆的月亮。朵朵想：月亮住在哪里呢？",
        "scene_summary": "夜晚的草地，天上有圆月和星星，左边有一棵大树，小兔子朵朵站在草地上仰头看月亮。",
        "plot_events": ["朵朵在草地上看见圆圆的月亮", "朵朵好奇月亮住在哪里"],
        "characters": ["c1"],
    },
    {
        "lines": ["朵朵走到小河边，发现月亮掉进了水里。", "乌龟爷爷慢慢地说：“那是月亮的影子。”"],
        "narration": "朵朵走呀走，来到了小河边。咦，月亮怎么掉进水里啦？乌龟爷爷慢慢地说：别着急，那是月亮的影子。",
        "scene_summary": "小河边，河水里映着月亮的倒影，朵朵站在左岸看着水面，乌龟爷爷在右岸。",
        "plot_events": ["朵朵来到小河边", "朵朵以为月亮掉进了水里", "乌龟爷爷告诉朵朵那是月亮的影子"],
        "characters": ["c1", "c2"],
    },
    {
        "lines": ["朵朵和乌龟爷爷一起坐在山坡上看月亮。", "原来，月亮一直在天上陪着大家。"],
        "narration": "后来，朵朵和乌龟爷爷一起坐在山坡上看月亮。原来呀，月亮一直在天上，陪着大家呢。",
        "scene_summary": "山坡上，朵朵和乌龟爷爷并排坐着看天上的大月亮，山坡下有一座亮着灯的小房子。",
        "plot_events": ["朵朵和乌龟爷爷一起在山坡上看月亮", "朵朵明白月亮一直在天上"],
        "characters": ["c1", "c2"],
    },
]


class Scene:
    """一页画面：边画边记录每个物体的坐标框。"""

    def __init__(self, page_no: int):
        self.page_no = page_no
        self.img = Image.new("RGB", (W, H))
        self.d = ImageDraw.Draw(self.img)
        self.objects: list[dict] = []

    def add(self, name: str, bbox, explanation: str) -> None:
        x1, y1, x2, y2 = (int(round(v)) for v in bbox)
        self.objects.append({"object_id": f"p{self.page_no}_o{len(self.objects) + 1}", "name": name,
                             "bbox": [max(0, x1), max(0, y1), min(W, x2), min(H, y2)],
                             "explanation": explanation})

    # ----- 背景 -----
    def night_sky(self, top=(18, 24, 68), bottom=(58, 84, 150)) -> None:
        for y in range(H):
            t = y / H
            color = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
            self.d.line([(0, y), (W, y)], fill=color)

    def star(self, cx: float, cy: float, r: float = 10, record: bool = False) -> None:
        pts = []
        for i in range(10):
            ang = -math.pi / 2 + i * math.pi / 5
            rad = r if i % 2 == 0 else r * 0.45
            pts.append((cx + rad * math.cos(ang), cy + rad * math.sin(ang)))
        self.d.polygon(pts, fill=(255, 240, 170))
        if record:
            self.add("星星", (cx - r, cy - r, cx + r, cy + r), "星星是离我们很远很远的大火球，所以看起来只有一点点亮。")

    def moon(self, cx: float, cy: float, r: float) -> None:
        self.d.ellipse((cx - r - 14, cy - r - 14, cx + r + 14, cy + r + 14), fill=(96, 120, 180))
        self.d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(255, 244, 190))
        for dx, dy, rr in ((-0.35, -0.2, 0.16), (0.3, 0.25, 0.12), (0.1, -0.45, 0.09)):
            x, y, q = cx + dx * r, cy + dy * r, rr * r
            self.d.ellipse((x - q, y - q, x + q, y + q), fill=(240, 226, 160))
        self.add("月亮", (cx - r, cy - r, cx + r, cy + r), "月亮是地球的卫星。它自己不会发光，是把太阳的光反射给我们。")

    # ----- 角色 -----
    def rabbit(self, cx: float, base_y: float, s: float = 1.0) -> None:
        d = self.d
        white, pink, blue = (250, 250, 250), (255, 190, 200), (60, 130, 220)
        body = (cx - 46 * s, base_y - 110 * s, cx + 46 * s, base_y)
        head = (cx - 40 * s, base_y - 180 * s, cx + 40 * s, base_y - 100 * s)
        for ex in (-20, 20):
            ear = (cx + (ex - 11) * s, base_y - 262 * s, cx + (ex + 11) * s, base_y - 165 * s)
            d.ellipse(ear, fill=white)
            d.ellipse((ear[0] + 5 * s, ear[1] + 14 * s, ear[2] - 5 * s, ear[3] - 12 * s), fill=pink)
        d.ellipse(body, fill=white)
        d.ellipse(head, fill=white)
        for ex in (-15, 15):
            d.ellipse((cx + (ex - 5) * s, base_y - 150 * s, cx + (ex + 5) * s, base_y - 140 * s), fill=(40, 40, 60))
        d.ellipse((cx - 5 * s, base_y - 132 * s, cx + 5 * s, base_y - 124 * s), fill=pink)
        scarf = (cx - 36 * s, base_y - 112 * s, cx + 36 * s, base_y - 92 * s)
        d.rounded_rectangle(scarf, radius=int(8 * s), fill=blue)
        tail = (cx + 14 * s, base_y - 92 * s, cx + 32 * s, base_y - 48 * s)
        d.rounded_rectangle(tail, radius=int(6 * s), fill=blue)
        for fx in (-26, 26):
            d.ellipse((cx + (fx - 16) * s, base_y - 14 * s, cx + (fx + 16) * s, base_y + 6 * s), fill=white)
        self.add("朵朵", (cx - 46 * s, base_y - 262 * s, cx + 46 * s, base_y + 6 * s),
                 "这是故事的主角朵朵，一只白色的小兔子，脖子上围着蓝色的围巾。")
        self.add("围巾", (scarf[0], scarf[1], scarf[2], tail[3]), "朵朵的蓝色围巾，围在脖子上很暖和。")

    def turtle(self, cx: float, base_y: float, s: float = 1.0, facing: int = -1) -> None:
        d = self.d
        skin, shell, line = (120, 180, 90), (40, 110, 70), (24, 80, 50)
        hx = cx + facing * 78 * s
        d.ellipse((hx - 26 * s, base_y - 78 * s, hx + 26 * s, base_y - 34 * s), fill=skin)
        d.ellipse((hx + facing * 8 * s - 4 * s, base_y - 64 * s, hx + facing * 8 * s + 4 * s, base_y - 56 * s),
                  fill=(30, 30, 30))
        for fx in (-48, 48):
            d.ellipse((cx + (fx - 16) * s, base_y - 22 * s, cx + (fx + 16) * s, base_y + 4 * s), fill=skin)
        shell_box = (cx - 70 * s, base_y - 96 * s, cx + 70 * s, base_y - 6 * s)
        d.chord(shell_box, 180, 360, fill=shell)
        d.rectangle((cx - 70 * s, base_y - 52 * s, cx + 70 * s, base_y - 14 * s), fill=shell)
        for gx in (-35, 0, 35):
            d.line((cx + gx * s, base_y - 90 * s, cx + gx * s, base_y - 16 * s), fill=line, width=3)
        d.line((cx - 66 * s, base_y - 52 * s, cx + 66 * s, base_y - 52 * s), fill=line, width=3)
        left = min(cx - 70 * s, hx - 26 * s)
        right = max(cx + 70 * s, hx + 26 * s)
        self.add("乌龟爷爷", (left, base_y - 96 * s, right, base_y + 4 * s),
                 "乌龟爷爷是一只年纪很大的乌龟，背着硬硬的龟壳，走路慢慢的。")

    # ----- 文字 -----
    def text_box(self, lines: list[str], font: ImageFont.FreeTypeFont) -> list[dict]:
        box = (90, 592, W - 90, 702)
        self.d.rounded_rectangle(box, radius=22, fill=(255, 252, 240), outline=(210, 196, 160), width=3)
        blocks = []
        y = box[1] + 14
        for line in lines:
            left, top, right, bottom = self.d.textbbox((0, 0), line, font=font)
            x = (W - (right - left)) // 2
            self.d.text((x, y), line, font=font, fill=(50, 44, 40))
            bx = self.d.textbbox((x, y), line, font=font)
            blocks.append({"text": line, "bbox": [int(v) for v in bx]})
            y += 44
        return blocks


def draw_page_1(font) -> tuple[Scene, list[dict]]:
    s = Scene(1)
    s.night_sky()
    for x, y, r in ((140, 90, 9), (330, 150, 7), (560, 70, 10), (760, 190, 8), (1180, 300, 9), (1120, 70, 7)):
        s.star(x, y, r)
    s.star(430, 250, 13, record=True)
    s.moon(980, 150, 82)
    s.d.ellipse((-200, 430, W + 200, 1100), fill=(46, 120, 84))
    s.d.rectangle((168, 300, 212, 520), fill=(110, 78, 50))
    for cx, cy, r in ((190, 250, 96), (120, 300, 70), (262, 300, 72)):
        s.d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(30, 96, 66))
    s.add("大树", (50, 154, 334, 520), "一棵高高的大树，圆圆的树冠像一把大伞。")
    s.rabbit(600, 520, 1.0)
    return s, s.text_box(PAGES_TEXT[0]["lines"], font)


def draw_page_2(font) -> tuple[Scene, list[dict]]:
    s = Scene(2)
    s.night_sky()
    for x, y, r in ((120, 70, 8), (420, 110, 7), (640, 50, 9), (1160, 150, 8)):
        s.star(x, y, r)
    s.moon(900, 120, 62)
    s.d.rectangle((0, 330, W, 430), fill=(52, 124, 86))
    s.d.rectangle((0, 430, W, 600), fill=(40, 96, 170))
    for y in (456, 500, 548):
        for x in range(40, W, 180):
            s.d.arc((x, y, x + 90, y + 18), 200, 340, fill=(120, 170, 230), width=3)
    s.add("小河", (0, 430, W, 600), "一条小河，河水轻轻地流。平静的水面像镜子一样，可以照出影子。")
    rx, ry, rw, rh = 640, 510, 78, 30
    s.d.ellipse((rx - rw, ry - rh, rx + rw, ry + rh), fill=(236, 228, 170))
    s.add("月亮的影子", (rx - rw, ry - rh, rx + rw, ry + rh),
          "这是月亮在水里的倒影。月亮没有掉下来，是水面把月亮照了出来。")
    for x in (1110, 1140, 1172):
        s.d.line((x, 420, x - 8, 330), fill=(96, 150, 70), width=6)
        s.d.ellipse((x - 16, 304, x, 344), fill=(132, 96, 60))
    s.add("芦苇", (1086, 304, 1178, 422), "长在水边的芦苇，细细长长，风一吹就会沙沙响。")
    s.rabbit(290, 426, 0.92)
    s.turtle(940, 424, 1.0, facing=-1)
    return s, s.text_box(PAGES_TEXT[1]["lines"], font)


def draw_page_3(font) -> tuple[Scene, list[dict]]:
    s = Scene(3)
    s.night_sky(top=(14, 18, 58), bottom=(70, 90, 160))
    for x, y, r in ((120, 110, 9), (300, 60, 7), (1010, 90, 9), (1180, 210, 8), (220, 250, 7)):
        s.star(x, y, r)
    s.moon(640, 160, 104)
    s.d.ellipse((120, 400, 1000, 1100), fill=(50, 128, 88))
    s.add("山坡", (120, 400, 1000, 592), "一个圆圆的小山坡，坐在上面可以看得很远。")
    hx, hy = 1100, 588
    s.d.rectangle((hx - 70, hy - 90, hx + 70, hy), fill=(196, 150, 104))
    s.d.polygon([(hx - 90, hy - 90), (hx, hy - 160), (hx + 90, hy - 90)], fill=(150, 70, 60))
    win = (hx - 26, hy - 64, hx + 26, hy - 20)
    s.d.rectangle(win, fill=(255, 220, 110))
    s.d.line((hx, hy - 64, hx, hy - 20), fill=(150, 110, 60), width=3)
    s.add("小房子", (hx - 90, hy - 160, hx + 90, hy), "山坡下的小房子，屋顶是红色的。")
    s.add("窗户", win, "亮着灯的窗户，说明家里有人在等着呢。")
    s.rabbit(520, 440, 0.86)
    s.turtle(700, 446, 0.9, facing=-1)
    return s, s.text_box(PAGES_TEXT[2]["lines"], font)


def build_book(font_info: dict) -> dict:
    font = ImageFont.truetype(font_info["path"], 32)
    (OUT / "pages").mkdir(parents=True, exist_ok=True)
    by_id = {c["character_id"]: c for c in CHARACTERS}
    pages = []
    for index, draw in enumerate((draw_page_1, draw_page_2, draw_page_3), start=1):
        scene, blocks = draw(font)
        rel = f"pages/page_{index:03d}.png"
        scene.img.save(OUT / rel, optimize=True)
        text = PAGES_TEXT[index - 1]
        pages.append({
            "book_id": BOOK_ID,
            "page_no": index,
            "page_image": rel,
            "image_size": {"width": W, "height": H},
            "ocr_text": {"full_text": "".join(text["lines"]), "blocks": blocks},
            "narration": text["narration"],
            "characters": [{"character_id": cid, "name": by_id[cid]["name"], "appearance": by_id[cid]["appearance"]}
                           for cid in text["characters"]],
            "objects": scene.objects,
            "scene_summary": text["scene_summary"],
            "plot_events": text["plot_events"],
            "video_clip_id": None,
            "time_range": None,
            "keyframes": [],
            "source_reference": {"origin": "self_made", "page_label": f"第 {index} 页"},
            "safety_check": {"status": "approved", "age_range": "3-6", "reviewer": "示例数据（队伍自制）",
                             "notes": "自制示例，内容为原创的温和睡前故事"},
        })
    return {
        "schema_version": SCHEMA_VERSION,
        "book_id": BOOK_ID,
        "title": "朵朵找月亮",
        "language": "zh",
        "age_range": "3-6",
        "source_reference": {
            "origin": "self_made",
            "description": "队伍自制的模拟绘本：画面由 scripts/make_sample_book.py 用几何图形绘制，文字为原创",
            "license": "队伍原创，仅用于本赛事开发联调",
            "page_text_font": font_info["name"],
        },
        "characters": CHARACTERS,
        "pages": pages,
        "video": None,
    }


def zoomed_box(bbox, zoom: float) -> list[int]:
    """画面以中心为原点放大 zoom 倍后，原坐标框的新位置（与本地降级视频的缓慢推近一致）。"""
    cx, cy = W / 2, H / 2
    x1, y1, x2, y2 = bbox
    out = [cx + (x1 - cx) * zoom, cy + (y1 - cy) * zoom, cx + (x2 - cx) * zoom, cy + (y2 - cy) * zoom]
    return [int(round(max(0, min(W, out[0])))), int(round(max(0, min(H, out[1])))),
            int(round(max(0, min(W, out[2])))), int(round(max(0, min(H, out[3]))))]


def add_video(book: dict) -> dict:
    """用本地降级方案（静态页图缓慢推近 + 占位提示音 + 字幕）合成示例视频，并按画面运动推算关键帧里的物体框。"""
    from agents.i2v_client import KENBURNS_ZOOM
    from agents.pipeline import build_pipeline
    from configs.settings import load_settings

    work = Path(tempfile.mkdtemp(prefix="pbsc_sample_"))
    env = {"PBSC_DATA_DIR": str(work / "data"), "PBSC_LOG_DIR": str(work / "logs")}   # 不带任何外部服务配置
    settings = load_settings(env, use_dotenv=False)
    settings.ensure_dirs()
    pipe = build_pipeline(settings)
    target = pipe.store.book_dir(BOOK_ID)
    shutil.copytree(OUT, target, ignore=shutil.ignore_patterns("video", "audio"))
    pipe.store.save(json.loads((target / "book.json").read_text(encoding="utf-8")))

    result = pipe.video.generate_book_video(BOOK_ID)
    produced = pipe.store.load(BOOK_ID)

    for sub in ("video", "audio"):
        if (OUT / sub).exists():
            shutil.rmtree(OUT / sub)
    for rel in [produced["video"]["path"], "video/clip_index.json"] + \
               [p["video_meta"]["clip_path"] for p in produced["pages"]] + \
               [p["narration_audio"]["path"] for p in produced["pages"]]:
        dest = OUT / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(target / rel, dest)

    book["video"] = produced["video"]
    for page, done in zip(book["pages"], produced["pages"]):
        for key in ("video_clip_id", "time_range", "video_prompt", "video_meta", "narration_audio"):
            page[key] = done.get(key)
        start, end = page["time_range"]
        clip_seconds = done["video_meta"]["raw_clip_seconds"]
        slowdown = done["video_meta"]["slowdown"]
        keyframes = []
        for fraction, label in ((0.25, "前段"), (0.7, "后段")):
            t = start + (end - start) * fraction
            source_t = min((t - start) / slowdown, clip_seconds)      # 成片时间换算回原片段时间（放慢后定格）
            zoom = 1 + KENBURNS_ZOOM * (source_t / clip_seconds)
            keyframes.append({
                "time": round(t, 2),
                "page_no": page["page_no"],
                "description": f"{page['scene_summary']}（{label}，画面缓慢推近）",
                "frame_size": {"width": W, "height": H},
                "objects": [{"name": o["name"], "bbox": zoomed_box(o["bbox"], zoom), "explanation": o["explanation"]}
                            for o in page["objects"]],
            })
        page["keyframes"] = keyframes
    shutil.rmtree(work, ignore_errors=True)
    print(f"示例视频：{result['video_path']}，时长 {result['duration']} 秒")
    return book


def main() -> int:
    parser = argparse.ArgumentParser(description="生成模拟绘本示例数据")
    parser.add_argument("--video", action="store_true", help="同时用本地降级方案合成示例视频并写入关键帧")
    args = parser.parse_args()

    font_info = find_cjk_font()
    if not font_info:
        print("找不到可显示中文的字体，请安装 Noto Sans SC 或用 PBSC_SUBTITLE_FONT 指定字体文件", file=sys.stderr)
        return 1
    book = build_book(font_info)
    if args.video:
        book = add_video(book)
    (OUT / "book.json").write_text(json.dumps(book, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已生成 {OUT}（{len(book['pages'])} 页，页面文字字体：{font_info['name']}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
