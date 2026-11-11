#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""参考后端（参考代码/story_tts_server.py）逐接口最小调用测试。

用法（在仓库根目录）：
    python scripts/smoke_reference_server.py
    python scripts/smoke_reference_server.py --out-md docs/A1-参考后端实测结果.md

做法：
- 不改动参考代码。把 TARGET_DIR 指到一个临时目录后导入 story_tts_server，
  用 Flask 自带的测试客户端逐个调用路由，所以不占用端口，也不会在参考代码目录里留下文件。
- 每个接口发一次“能触发主流程的最小请求”，记录 HTTP 状态、耗时、返回摘要，并归类：
    已跑通            本机即可完成，返回正确
    依赖外部API待验证  需要外部服务（故事/TTS/图生视频等），当前环境没有配置真实地址和密钥
    不可用            不依赖外部服务却失败，或返回与预期不符
- 外部服务的环境变量如果已经配置（不是占位符 xxxx），同一脚本会真实调用，
  结果会变成“已跑通”或暴露真实错误；这时会产生费用，请留意。
"""
from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import struct
import sys
import tempfile
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

ROOT = Path(__file__).resolve().parent.parent
REF_DIR = ROOT / "参考代码"

# 参考后端用到的外部服务及其环境变量（见 参考代码/config.py）
EXTERNAL_ENV = {
    "故事大模型": ["SIMPLE_STORY_WRITER_URL", "SIMPLE_STORY_WRITER_API_KEY"],
    "TTS": ["TTS_API_URL"],
    "语音克隆TTS": ["VOICE_CLONE_TTS_API_URL"],
    "声音注册": ["ENROLL_SERVER_URL"],
    "NLU问答模型": ["NLU_URL", "NLU_API_KEY"],
    "图像理解VLM": ["IMAGE_ANALYSIS_URL", "IMAGE_API_KEY"],
    "文生图": ["TEXT2IMAGE_URL", "TEXT2IMAGE_API_KEY"],
    "图生视频": ["IMAGE2VIDEO_URL", "IMAGE2VIDEO_API_KEY"],
    "OSS图床": ["OSS_ACCESS_KEY_ID", "OSS_ACCESS_KEY_SECRET", "OSS_BUCKET", "OSS_ENDPOINT"],
    "音效生成": ["TEXT2AUDIO_URL", "TEXT2AUDIO_API_KEY"],
    "音乐生成": ["TEXT2MUSIC_URL", "TEXT2MUSIC_API_KEY"],
    "ASR": ["ASR_WS_SERVER_URL"],
}

_SCRUB: list[str] = []

PASS = "已跑通"
EXTERNAL = "依赖外部API待验证"
BROKEN = "不可用"


def service_configured(name: str) -> bool:
    return all(os.environ.get(k, "xxxx") not in ("", "xxxx") for k in EXTERNAL_ENV[name])


@dataclass
class Case:
    name: str
    method: str
    path: str
    purpose: str
    external: list[str] = field(default_factory=list)   # 依赖的外部服务
    json_body: Optional[dict] = None
    form: Optional[dict] = None                           # multipart 表单
    ok: Optional[Callable[[int, Any], bool]] = None       # 判断“业务上成功”
    note: str = ""


@dataclass
class Result:
    case: Case
    http_status: int
    elapsed_ms: float
    category: str
    excerpt: str
    remark: str = ""


def make_wav(seconds: float = 0.5, rate: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(struct.pack("<h", 0) * int(seconds * rate))
    return buf.getvalue()


def excerpt_of(resp) -> tuple[Any, str]:
    ctype = resp.headers.get("Content-Type", "")
    if "json" in ctype:
        try:
            data = resp.get_json()
        except Exception:
            data = None
        text = json.dumps(data, ensure_ascii=False)
    elif "event-stream" in ctype or ctype.startswith("text/"):
        data = resp.get_data(as_text=True)
        text = data
    else:
        data = None
        text = f"<{ctype or '二进制'}，{len(resp.get_data())} 字节>"
    text = " ".join(text.split())
    for raw in _SCRUB:                       # 结果表要入库，去掉本机临时目录（含用户名）
        text = text.replace(raw, "<临时目录>")
    return data, (text[:150] + "…" if len(text) > 150 else text)


def default_ok(status: int, data: Any) -> bool:
    if status >= 400:
        return False
    if isinstance(data, dict) and data.get("success") is False:
        return False
    return True


def run_case(client, case: Case) -> Result:
    kwargs: dict[str, Any] = {}
    if case.json_body is not None:
        kwargs["json"] = case.json_body
    if case.form is not None:
        kwargs["data"] = case.form
        kwargs["content_type"] = "multipart/form-data"
    start = time.perf_counter()
    try:
        resp = client.open(case.path, method=case.method, **kwargs)
        data, excerpt = excerpt_of(resp)
        status = resp.status_code
    except Exception as exc:  # 测试客户端里路由直接抛异常
        status, data = 599, None
        excerpt = f"路由抛出异常 {type(exc).__name__}: {exc}"[:150]
    elapsed = (time.perf_counter() - start) * 1000

    ok = (case.ok or default_ok)(status, data)
    unconfigured = [s for s in case.external if not service_configured(s)]
    remark = case.note
    if ok and not unconfigured:
        category = PASS
    elif unconfigured:
        category = EXTERNAL
        missing = "、".join(unconfigured)
        remark = (remark + "；" if remark else "") + f"未配置：{missing}"
        if ok:
            remark += "（接口仍返回成功，属于静默失败，见清单说明）"
    else:
        category = BROKEN
    return Result(case, status, elapsed, category, excerpt, remark)


def build_cases(tmp: Path, session_dir: str) -> list[Case]:
    img = str(tmp / "fixtures" / "page.png")
    vid = str(tmp / "fixtures" / "clip.mp4")
    wav_bytes = make_wav()
    has_text = lambda key: (lambda s, d: s < 400 and isinstance(d, dict) and bool(d.get(key)))  # noqa: E731

    return [
        # ---------- 本机即可完成 ----------
        Case("健康检查", "GET", "/health", "服务存活检查"),
        Case("主页面", "GET", "/tts_v2.html", "托管前端主页面"),
        Case("主页面(带前缀)", "GET", "/tts-story-book/tts_v2.html", "部署前缀下的主页面"),
        Case("声音注册页", "GET", "/voice_register.html", "托管声音注册页面"),
        Case("自定义音色列表", "GET", "/api/custom-voices", "列出 prompt_dir 下已注册音色"),
        Case("书架列表", "GET", "/api/bookshelf", "读取 bookshelf.json"),
        Case("保存故事文本", "POST", "/api/save-story-text", "把故事文本和参数写入会话目录",
             json_body={"session_dir": session_dir, "story_text": "测试故事。\n\n第二段。",
                        "story_params": {"story_topic": "测试", "main_role": "小兔子", "scene": "森林"}}),
        Case("加入书架", "POST", "/api/bookshelf", "复制会话目录到 bookshelf/ 并登记",
             json_body={"book_name": "冒烟测试绘本", "session_dir": session_dir,
                        "story_params": {"story_topic": "测试"}}),
        Case("从书架加载", "POST", "/api/bookshelf/load", "读取会话目录下的文本、图片、音频、视频清单",
             json_body={"session_dir": session_dir}),
        Case("文件是否存在", "POST", "/api/file-exists", "前端用它轮询视频文件是否生成",
             json_body={"file_path": "fixtures/page.png"},
             ok=lambda s, d: s < 400 and isinstance(d, dict) and d.get("exists") is True),
        Case("保存答题结果", "POST", "/api/save-exam-result", "写入 exam/exam_result.json",
             json_body={"session_dir": session_dir, "user_answers": {"0": "A"}, "exam_completed": True}),
        Case("读取音频文件", "GET", "/api/audio/fixtures/q.wav", "按相对路径返回音频"),
        Case("读取图片文件", "GET", "/api/image/fixtures/page.png", "按相对路径返回图片"),
        Case("读取视频文件", "GET", "/api/video/fixtures/clip.mp4", "按相对路径返回视频"),
        Case("历史静态资源", "GET", "/api/history_res/assistant3.png", "返回 history_res 下的资源"),
        Case("前端实际请求的地址", "GET", "/tts-story-book/api/bookshelf",
             "tts_v2.html 里所有请求都带 /tts-story-book 前缀",
             note="后端没有注册带前缀的 /api 路由，本机直接运行时前端请求会 404"),
        # ---------- 依赖外部服务 ----------
        Case("生成故事(简易)", "POST", "/api/generate-story", "一次性生成整篇故事", ["故事大模型"],
             json_body={"story_topic": "分享", "main_role": "小猫", "scene": "幼儿园"}),
        Case("生成故事(深度思考)", "POST", "/api/generate-story", "多智能体：提问-专家-大纲-分章", ["故事大模型"],
             json_body={"story_topic": "分享", "main_role": "小猫", "scene": "幼儿园", "deep_think": True}),
        Case("流式生成故事", "POST", "/api/generate-story-stream", "SSE 逐段返回故事", ["故事大模型"],
             json_body={"story_topic": "分享", "main_role": "小猫", "scene": "幼儿园"},
             ok=lambda s, d: s < 400 and isinstance(d, str) and '"type": "complete"' in d),
        Case("语音合成", "POST", "/api/tts", "文本转语音", ["TTS"],
             json_body={"text": "你好，小朋友。", "voice": "xiaosi"}),
        Case("单段语音", "POST", "/api/generate-audio", "为一段文本生成语音", ["TTS"],
             json_body={"text": "你好，小朋友。", "voice": "xiaosi", "session_dir": session_dir}),
        Case("段落音频(含音效)", "POST", "/api/generate-paragraph-audio", "TTS 与音效并行生成后拼接",
             ["TTS", "音效生成"],
             json_body={"items": [["小兔子跳过小溪", 0], ["水花声：哗啦", 1]], "voice": "xiaosi",
                        "session_dir": session_dir, "paragraph_index": 0}),
        Case("音效生成", "POST", "/api/generate-sfx", "按描述生成音效", ["音效生成"],
             json_body={"prompt": "雨声", "duration": 2, "session_dir": session_dir}),
        Case("背景音乐", "POST", "/api/generate-music", "按故事内容生成背景音乐", ["音乐生成"],
             json_body={"prompt": "温暖的睡前故事", "session_dir": session_dir, "duration": 5},
             ok=has_text("music_path")),
        Case("配图生成", "POST", "/api/generate-image", "文生图（吉卜力风格）", ["文生图"],
             json_body={"prompt": "小兔子在森林里", "index": 0, "session_dir": session_dir}),
        Case("视频生成(异步)", "POST", "/api/generate-video", "提交图生视频任务，立即返回",
             ["图生视频", "OSS图床"],
             json_body={"prompt": "小兔子轻轻眨眼", "image_path": img, "session_dir": session_dir,
                        "paragraph_index": 0},
             note="只表示任务已提交；没有任务状态接口，失败时不会通知调用方"),
        Case("图片主体识别", "POST", "/api/analyze-image", "VLM 返回物体名称和坐标框", ["图像理解VLM"],
             json_body={"image_path": img}),
        Case("视频帧识别", "POST", "/api/analyze-video-frame", "截取视频帧后交给 VLM 识别", ["图像理解VLM"],
             json_body={"video_path": vid, "time_seconds": 0.2},
             note="截帧在本机完成，识别依赖 VLM"),
        Case("百科问答生成", "POST", "/api/generate-baike", "按段落生成百科问答", ["故事大模型"],
             json_body={"chapter": "小兔子在森林里发现了一颗松果。", "session_dir": session_dir}),
        Case("试卷生成", "POST", "/api/generate-exam", "按故事和百科问答出选择题", ["故事大模型"],
             json_body={"story": "小兔子在森林里发现了一颗松果。",
                        "baike_qas": [{"question": "松果是什么？", "answer": "松树的果实"}]}),
        Case("问答(NLU)", "POST", "/api/nlu", "语音小助手回答问题", ["NLU问答模型"],
             json_body={"question": "小兔子喜欢吃什么？"}),
        Case("语音识别", "POST", "/api/asr", "上传 wav，返回识别文本", ["ASR"],
             form={"audio": (io.BytesIO(wav_bytes), "q.wav")},
             ok=has_text("text")),
        Case("声音注册", "POST", "/api/enroll", "上传录音注册自定义音色", ["声音注册"],
             form={"audio": (io.BytesIO(wav_bytes), "v.wav"), "text": "今天天气真好", "spkname": "smoke"},
             ok=lambda s, d: s < 400),
        Case("移出书架", "POST", "/api/bookshelf/remove", "从 bookshelf.json 删除并可删目录",
             json_body={"session_dir": "__由脚本替换__"}),
    ]


def prepare_fixtures(tmp: Path) -> None:
    fx = tmp / "fixtures"
    fx.mkdir(parents=True, exist_ok=True)
    from PIL import Image
    Image.new("RGB", (320, 180), (200, 220, 255)).save(fx / "page.png")
    (fx / "q.wav").write_bytes(make_wav())
    import cv2
    import numpy as np
    writer = cv2.VideoWriter(str(fx / "clip.mp4"), cv2.VideoWriter_fourcc(*"mp4v"), 10, (320, 180))
    for i in range(10):
        frame = np.full((180, 320, 3), 40 + i * 10, dtype=np.uint8)
        writer.write(frame)
    writer.release()
    shutil.copy(REF_DIR / "bookshelf.json", tmp / "bookshelf.json")
    shutil.copytree(REF_DIR / "history_res", tmp / "history_res", dirs_exist_ok=True)


def to_markdown(results: list[Result], meta: dict) -> str:
    lines = [
        "# A1 参考后端逐接口实测结果（脚本自动生成）",
        "",
        "> 由 `scripts/smoke_reference_server.py` 生成，请勿手工修改；重新运行脚本即可刷新。",
        "",
        f"- 测试时间：{meta['time']}",
        f"- 运行环境：{meta['platform']}，Python {meta['python']}",
        f"- 依赖版本：{meta['packages']}",
        f"- 已配置的外部服务：{meta['configured'] or '无（全部为占位符 xxxx）'}",
        "- 调用方式：Flask 测试客户端，单次调用；耗时为本机处理时间，不含网络。",
        "",
        "| 序号 | 接口 | 方法 | 路径 | 用途 | 依赖的外部服务 | 结论 | HTTP | 单次耗时 | 费用 | 返回摘要 / 说明 |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for i, r in enumerate(results, 1):
        c = r.case
        ext = "、".join(c.external) if c.external else "无"
        if not c.external:
            cost = "0（本机）"
        elif r.category == PASS:
            cost = "已真实调用，按服务商计费"
        else:
            cost = "未产生（没有真实调用）"
        detail = r.excerpt.replace("|", "\\|")
        if r.remark:
            detail = f"**{r.remark}**。{detail}"
        lines.append(
            f"| {i} | {c.name} | {c.method} | `{c.path}` | {c.purpose} | {ext} | {r.category} | "
            f"{r.http_status} | {r.elapsed_ms:.0f} ms | {cost} | {detail} |"
        )
    counts: dict[str, int] = {}
    for r in results:
        counts[r.category] = counts.get(r.category, 0) + 1
    lines += ["", "## 汇总", ""]
    for k in (PASS, EXTERNAL, BROKEN):
        lines.append(f"- {k}：{counts.get(k, 0)} 个")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out-md", default=str(ROOT / "docs" / "A1-参考后端实测结果.md"))
    parser.add_argument("--out-json", default="")
    args = parser.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="pbsc_ref_smoke_"))
    os.environ["TARGET_DIR"] = str(tmp).replace("\\", "/")
    _SCRUB.extend([str(tmp).replace("\\", "/"), str(tmp).replace("\\", "\\\\"), str(tmp)])
    prepare_fixtures(tmp)
    os.chdir(tmp)                      # 参考后端有几处写相对路径，避免写进仓库
    sys.path.insert(0, str(REF_DIR))

    import importlib.metadata as md
    import platform

    import story_tts_server as ref     # noqa: E402  导入时会创建 prompt_dir 等目录

    client = ref.app.test_client()

    # 先建会话目录，后面的接口要用
    start = time.perf_counter()
    resp = client.post("/api/create-session", json={})
    data, excerpt = excerpt_of(resp)
    session_dir = (data or {}).get("session_dir", "")
    leaked_abs = os.path.isabs(session_dir)
    create_case = Case("创建会话目录", "POST", "/api/create-session", "创建 output_dir/<时间戳>/ 及子目录",
                       note=("返回了绝对路径：代码用 '/' 拼接后做字符串替换，Windows 下替换不生效"
                             if leaked_abs else ""))
    results = [Result(create_case, resp.status_code, (time.perf_counter() - start) * 1000,
                      PASS if resp.status_code < 400 and session_dir else BROKEN, excerpt, create_case.note)]

    cases = build_cases(tmp, session_dir)
    shelf_session = ""
    for case in cases:
        if case.name == "移出书架":
            case.json_body = {"session_dir": shelf_session or session_dir}
        result = run_case(client, case)
        if case.name == "加入书架":
            try:
                body = json.loads(json.dumps(client.get("/api/bookshelf").get_json()))
                shelf_session = body["bookshelf"][-1]["session_dir"]
            except Exception:
                shelf_session = ""
        results.append(result)
        print(f"[{result.category}] {case.method} {case.path}  {result.http_status}  {result.elapsed_ms:.0f}ms")

    pkgs = []
    for name in ("flask", "flask-cors", "requests", "pydantic", "numpy", "pillow", "opencv-python",
                 "websockets", "sseclient-py", "tqdm"):
        try:
            pkgs.append(f"{name} {md.version(name)}")
        except md.PackageNotFoundError:
            pkgs.append(f"{name} 未安装")
    try:
        md.version("oss2")
        pkgs.append("oss2 已安装")
    except md.PackageNotFoundError:
        pkgs.append("oss2 未安装（视频生成的图片上传不可用）")

    meta = {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "packages": "，".join(pkgs),
        "configured": "、".join(n for n in EXTERNAL_ENV if service_configured(n)),
    }
    out_md = Path(args.out_md)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(to_markdown(results, meta), encoding="utf-8")
    if args.out_json:
        payload = [{"name": r.case.name, "method": r.case.method, "path": r.case.path,
                    "external": r.case.external, "category": r.category, "http_status": r.http_status,
                    "elapsed_ms": round(r.elapsed_ms, 1), "excerpt": r.excerpt, "remark": r.remark}
                   for r in results]
        Path(args.out_json).write_text(json.dumps({"meta": meta, "results": payload}, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
    print(f"\n结果已写入 {out_md}")
    os.chdir(ROOT)
    shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
