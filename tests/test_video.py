# -*- coding: utf-8 -*-
"""视频生成（A 步骤 5）：字幕与时间轴计算、TTS 与图生视频客户端协议、整本合成与写回、缓存与预算。"""
from __future__ import annotations

import json
import wave

import pytest

from agents.i2v_client import GatewayI2V
from agents.pipeline import build_pipeline
from agents.tts_client import TTSClient, estimate_speech_seconds
from agents.video_compose import plan_timeline, plan_timing, probe_duration, schedule_subtitles, split_subtitles
from server.errors import ExternalServiceError, ServiceNotConfigured

from conftest import make_png_bytes, make_settings, service_env


# ---------------------------------------------------------------- 纯计算
def test_split_subtitles_keeps_quotes_and_limits_length():
    text = "朵朵走呀走，来到了小河边。咦，月亮怎么掉进水里啦？乌龟爷爷慢慢地说：“别着急，那是月亮的影子。”"
    units = split_subtitles(text, max_chars=14)
    assert "".join(units) == text
    assert all(len(u) <= 14 for u in units)
    assert units[-1].endswith("。”")                 # 句号和后引号不拆开


def test_schedule_subtitles_fills_speech_duration():
    cues = schedule_subtitles(["朵朵看见了月亮。", "真圆呀！"], 6.0)
    assert cues[0]["start"] == 0.0 and cues[-1]["end"] == pytest.approx(6.0, abs=0.01)
    assert cues[0]["end"] == cues[1]["start"]
    assert (cues[0]["end"] - cues[0]["start"]) > (cues[1]["end"] - cues[1]["start"])   # 字多的显示更久
    assert schedule_subtitles([], 3.0) == [] and schedule_subtitles(["a"], 0) == []


def test_plan_timing_slow_then_freeze():
    args = dict(tail_padding=0.8, min_page_seconds=3.0, max_slowdown=1.25)
    assert plan_timing(5.0, 3.0, **args) == {"page_seconds": 3.8, "slowdown": 1.0, "freeze_seconds": 0.0}
    assert plan_timing(5.0, 5.0, **args) == {"page_seconds": 5.8, "slowdown": 1.16, "freeze_seconds": 0.0}
    long = plan_timing(5.0, 10.0, **args)
    assert long["page_seconds"] == 10.8 and long["slowdown"] == 1.25 and long["freeze_seconds"] == 4.55
    assert plan_timing(5.0, 0.0, **args)["page_seconds"] == 3.0                      # 没有旁白时用最短页时长


def test_plan_timeline_ranges_are_contiguous():
    t = plan_timeline([10.0, 8.0, 6.0], crossfade=0.5)
    assert t["time_ranges"] == [[0.0, 9.5], [9.5, 17.0], [17.0, 23.0]] and t["duration"] == 23.0
    assert plan_timeline([4.0], 0.5) == {"duration": 4.0, "starts": [0.0], "time_ranges": [[0.0, 4.0]],
                                         "crossfade": 0.0}
    assert plan_timeline([1.0, 0.6], 0.5)["crossfade"] == 0.3                        # 转场不超过最短页的一半


# ---------------------------------------------------------------- TTS 客户端
def test_tts_placeholder_is_marked(tmp_path):
    result = TTSClient(None).synthesize("朵朵看见了月亮。真圆呀！", tmp_path / "a.wav")
    assert result.is_placeholder and result.provider == "placeholder_tone"
    with wave.open(str(result.path)) as wf:
        assert wf.getnframes() / wf.getframerate() == pytest.approx(result.duration, abs=0.01)
    assert result.duration == estimate_speech_seconds("朵朵看见了月亮。真圆呀！")


def test_tts_reference_protocol(tmp_path, stub):
    result = TTSClient(f"{stub.base_url}/tts").synthesize("你好 <b>&", tmp_path / "t.wav", voice="xiaosi")
    assert not result.is_placeholder and result.duration == pytest.approx(0.8, abs=0.01)
    sent = stub.tts_requests[0]
    assert sent["voice_params"]["voice_type"] == "xiaosi" and sent["voice_params"]["audio_format"] == "wav"
    assert sent["text"].startswith('<speak voice_type="xiaosi"') and "&lt;b&gt;&amp;" in sent["text"]

    stub.tts_status = 503
    with pytest.raises(ExternalServiceError) as err:
        TTSClient(f"{stub.base_url}/tts").synthesize("你好", tmp_path / "t2.wav")
    assert err.value.retryable and not (tmp_path / "t2.wav").exists()


# ---------------------------------------------------------------- 图生视频客户端
def gateway(stub, **kw) -> GatewayI2V:
    return GatewayI2V(stub.base_url, "test-key", "stub-i2v", poll_interval=0.01, max_wait=5, **kw)


def test_i2v_gateway_submit_poll_download(tmp_path, stub):
    image = tmp_path / "page.png"
    image.write_bytes(make_png_bytes((640, 360)))
    stub.poll_script = [2, 2, 3]
    messages = []
    result = gateway(stub).generate(image, "朵朵轻轻眨眼", tmp_path / "out.mp4", seconds=1, on_progress=messages.append)

    assert result.path.read_bytes() == stub.clip_bytes and result.seconds == pytest.approx(1.0, abs=0.15)
    assert result.billable and not result.is_fallback and result.task_id == "stub-task-1"
    assert stub.poll_count == 3 and any("生成中" in m for m in messages)
    # 请求形状与参考代码一致：首帧以 data URL 放在 content 里，密钥走 api-key 头
    body, headers = stub.i2v_submits[0], stub.i2v_headers[0]
    assert body["model"] == "stub-i2v" and body["content"][0] == {"type": "text", "text": "朵朵轻轻眨眼"}
    assert body["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert headers["api-key"] == "test-key" and headers["x-apx-model"] == "stub-i2v"


def test_i2v_gateway_failure_modes(tmp_path, stub):
    image = tmp_path / "page.png"
    image.write_bytes(make_png_bytes())
    stub.poll_script = [4]
    with pytest.raises(ExternalServiceError, match="内容审核未通过"):
        gateway(stub).generate(image, "p", tmp_path / "a.mp4")

    stub.poll_script, stub.poll_count = [2], 0
    slow = GatewayI2V(stub.base_url, "k", "m", poll_interval=0.01, max_wait=0.05)
    with pytest.raises(ExternalServiceError, match="仍未完成"):
        slow.generate(image, "p", tmp_path / "b.mp4")

    stub.submit_status = 500
    with pytest.raises(ExternalServiceError) as err:
        gateway(stub).generate(image, "p", tmp_path / "c.mp4")
    assert err.value.retryable and err.value.status == 500

    with pytest.raises(ServiceNotConfigured):
        GatewayI2V(None, None, None).generate(image, "p", tmp_path / "d.mp4")
    with pytest.raises(ServiceNotConfigured):                       # url 传图模式但没有公网地址
        gateway(stub, image_mode="url").build_payload(image, "p", 5)
    url_mode = gateway(stub, image_mode="url", public_url_for=lambda p: "https://demo.example/p.png")
    assert url_mode.build_payload(image, "p", 5) == {
        "model": "stub-i2v", "prompt": "p", "duration": 5, "resolution": "720p", "aspect_ratio": "16:9",
        "img_url": "https://demo.example/p.png", "extra_body": None}


# ---------------------------------------------------------------- 整本合成（本地降级）
@pytest.fixture
def local_pipe(tmp_path, sample_dir):
    pipe = build_pipeline(make_settings(tmp_path))
    pipe.store.install_sample(sample_dir)
    return pipe


def test_book_video_with_fallback_writes_back_everything(local_pipe):
    pipe = local_pipe
    result = pipe.video.generate_book_video("demo_book")
    book = pipe.store.load("demo_book")
    book_dir = pipe.store.book_dir("demo_book")

    # 整本视频存在，时长与时间轴一致；各页 time_range 首尾相接
    assert probe_duration(book_dir / result["video_path"]) == pytest.approx(result["duration"], abs=0.2)
    ranges = [p["time_range"] for p in book["pages"]]
    assert ranges[0][0] == 0.0 and ranges[-1][1] == result["duration"]
    assert all(ranges[i][1] == ranges[i + 1][0] for i in range(len(ranges) - 1))

    # 每页写回：片段号、提示词、旁白配音、片段信息；降级和占位都如实标注
    for page in book["pages"]:
        meta = page["video_meta"]
        assert page["video_clip_id"] == f"demo_book_p{page['page_no']:03d}"
        assert (book_dir / meta["clip_path"]).is_file() and (book_dir / page["narration_audio"]["path"]).is_file()
        assert meta["is_fallback"] and meta["provider"] == "local_kenburns" and "未配置" in meta["fallback_reason"]
        assert page["narration_audio"]["is_placeholder"]
        assert "保持原图的画风" in page["video_prompt"] and page["scene_summary"] in page["video_prompt"]
        assert meta["subtitles"] and meta["subtitles"][-1]["end"] <= meta["clip_duration"]
    assert "乌龟爷爷（绿色的老乌龟" in book["pages"][1]["video_prompt"]            # 用了 B 的人物外观设定
    assert "上一页是" in book["pages"][1]["video_prompt"] and "下一页是" in book["pages"][1]["video_prompt"]
    assert "这是全书第一页" in book["pages"][0]["video_prompt"]
    assert result["fallback_pages"] == [1, 2, 3] and result["placeholder_audio_pages"] == [1, 2, 3]

    # 片段索引（给 B 建检索用）与分页数据一致
    index = json.loads((book_dir / "video/clip_index.json").read_text(encoding="utf-8"))
    assert [c["page_no"] for c in index["clips"]] == [1, 2, 3]
    assert [c["time_range"] for c in index["clips"]] == ranges
    assert index["clips"][0]["narration"] == book["pages"][0]["narration"] and index["clips"][0]["prompt"]

    # 运行记录：整本 → 单页 → 配音/片段，逐级可追溯
    top = pipe.runs.find(result["run_id"])
    page_run = pipe.runs.find(book["pages"][0]["video_meta"]["run_id"])
    i2v_run = pipe.runs.find(book["pages"][0]["video_meta"]["i2v_run_id"])
    assert top["kind"] == "video_book" and page_run["parent_run_id"] == top["run_id"]
    assert i2v_run["parent_run_id"] == page_run["run_id"] and i2v_run["prompt"]["version"] == "video_v1"
    assert i2v_run["input"]["first_frame"] == "pages/page_001.png" and i2v_run["notes"]["degraded"] is True
    assert any(o["role"] == "book_video" for o in top["outputs"])

    # 费用台账：本地降级不收费
    entries = pipe.ledger.entries()
    assert len(entries) == 6 and not any(e["billable"] for e in entries)

    # 再跑一遍：输入没变，片段和配音都走缓存，不产生新的调用
    again = pipe.video.generate_book_video("demo_book")
    assert len(pipe.ledger.entries()) == 6 and again["duration"] == result["duration"]


def test_single_page_clip_and_stale_flag(local_pipe):
    pipe = local_pipe
    pipe.video.generate_book_video("demo_book")
    assert pipe.store.load("demo_book")["video"]["stale"] is False

    def change(b):
        b["pages"][1]["narration"] = "朵朵来到小河边，乌龟爷爷告诉她，水里的是月亮的影子。"

    pipe.store.update("demo_book", change)
    clip = pipe.video.generate_page_clip("demo_book", 2)
    book = pipe.store.load("demo_book")
    assert clip["raw_cached"] is True                                   # 旁白变了，但图生视频片段不必重做
    assert book["video"]["stale"] is True                               # 整本视频需要重新拼接
    assert book["pages"][1]["narration_audio"]["path"] == "audio/page_002.wav"


def test_page_without_narration_uses_original_text(local_pipe):
    pipe = local_pipe
    pipe.store.update("demo_book", lambda b: b["pages"][0].update(narration=""))
    pipe.video.generate_page_clip("demo_book", 1)
    assert pipe.store.load("demo_book")["pages"][0]["video_meta"]["narration_source"] == "ocr_passthrough"


# ---------------------------------------------------------------- 接真实协议（模拟服务）
def test_book_video_through_gateway_and_tts(tmp_path, sample_dir, stub):
    stub.poll_script = [2, 3]
    pipe = build_pipeline(make_settings(tmp_path, **service_env(stub, llm=False)))
    pipe.store.install_sample(sample_dir)
    result = pipe.video.generate_book_video("demo_book")
    book = pipe.store.load("demo_book")

    assert result["fallback_pages"] == [] and result["placeholder_audio_pages"] == []
    assert len(stub.i2v_submits) == 3 and len(stub.tts_requests) == 3
    assert all(p["video_meta"]["provider"] == "reference_i2v_gateway" and p["video_meta"]["model"] == "stub-i2v"
               for p in book["pages"])
    assert book["pages"][0]["video_prompt"] == stub.i2v_submits[0]["content"][0]["text"]
    billable = [e for e in pipe.ledger.entries() if e["billable"]]
    assert sorted(e["service"] for e in billable) == ["i2v"] * 3 + ["tts"] * 3
    assert all(e["amount"] is None and not e["priced"] for e in billable)            # 没填单价：只记用量
    record = json.dumps(pipe.runs.find(book["pages"][0]["video_meta"]["i2v_run_id"]), ensure_ascii=False)
    assert "test-key" not in record

    pipe.video.generate_book_video("demo_book")                                       # 缓存命中，不再调用
    assert len(stub.i2v_submits) == 3 and len(stub.tts_requests) == 3
    pipe.video.generate_page_clip("demo_book", 1, force=True)                         # 强制重做才会再花钱
    assert len(stub.i2v_submits) == 4


def test_gateway_failure_and_budget_fall_back_with_reason(tmp_path, sample_dir, stub):
    pricing = tmp_path / "pricing.json"
    pricing.write_text(json.dumps({"services": {}, "budgets": {"i2v": {"max_calls_per_day": 1}}}), encoding="utf-8")
    pipe = build_pipeline(make_settings(tmp_path, PBSC_PRICING_FILE=str(pricing),
                                        **service_env(stub, llm=False, tts=False)))
    pipe.store.install_sample(sample_dir)

    stub.poll_script = [4]                                               # 第 1 页：服务端任务失败
    first = pipe.video.generate_page_clip("demo_book", 1)
    meta = pipe.store.load("demo_book")["pages"][0]["video_meta"]
    assert first["is_fallback"] and "图生视频调用失败" in meta["fallback_reason"]
    run = pipe.runs.find(meta["i2v_run_id"])
    assert run["notes"]["gateway_error"]["code"] == "EXTERNAL_SERVICE_ERROR" and run["notes"]["degraded"] is True

    stub.poll_script, stub.poll_count = [3], 0                           # 第 2 页：成功，用掉当天唯一的额度
    assert pipe.video.generate_page_clip("demo_book", 2)["is_fallback"] is False

    third = pipe.video.generate_page_clip("demo_book", 3)                # 第 3 页：预算用完，不再调用收费服务
    assert third["is_fallback"]
    assert "BUDGET_EXCEEDED" in pipe.store.load("demo_book")["pages"][2]["video_meta"]["fallback_reason"]
    assert len(stub.i2v_submits) == 2

    # 降级片段不会占住缓存：服务恢复后重跑第 1 页会再次尝试真实服务（这里预算已满，仍是降级）
    assert pipe.video.generate_page_clip("demo_book", 1)["raw_cached"] is False
