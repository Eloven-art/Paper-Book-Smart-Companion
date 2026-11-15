# -*- coding: utf-8 -*-
"""有依据问答：检索、依据校验、真实接口与历史落盘。"""
from __future__ import annotations

import json

import pytest

from conftest import ROOT, make_settings, service_env

API = "/api/v1"


def body_of(resp) -> dict:
    data = resp.get_json()
    assert data["status"] == ("ok" if data["error"] is None else "error")
    return data


@pytest.fixture
def book() -> dict:
    return json.loads((ROOT / "data" / "samples" / "demo_book" / "book.json").read_text(encoding="utf-8"))


def test_retrieval_prefers_pages_with_matching_text(tmp_path, book):
    from agents.pipeline import build_pipeline
    pipeline = build_pipeline(make_settings(tmp_path))
    chosen = pipeline.qa.retrieve(book, 1, "乌龟爷爷说了什么？")
    assert chosen and chosen[0][0] == 2                 # “乌龟爷爷”只出现在第 2 页

    chosen = pipeline.qa.retrieve(book, 1, "恐龙吃什么？")
    assert {no for no, _ in chosen} <= {1, 2}           # 无命中时退化为当前页及相邻页


def test_evidence_quote_must_exist_in_page(tmp_path, book):
    from agents.pipeline import build_pipeline
    pipeline = build_pipeline(make_settings(tmp_path))
    quote = "乌龟爷爷慢慢地说：“那是月亮的影子。”"
    assert pipeline.qa._validate_evidence(book, [{"page_no": 2, "quote": quote}]) == [
        {"page_no": 2, "quote": quote}]
    # 模型编造的依据会被丢弃，而不是原样返回
    assert pipeline.qa._validate_evidence(book, [{"page_no": 2, "quote": "恐龙在草地上吃草。"}]) == []
    assert pipeline.qa._validate_evidence(book, [{"page_no": 99, "quote": quote}]) == []


def test_real_qa_grounded_ungrounded_and_history(tmp_path, stub):
    from server.app import create_app
    settings = make_settings(tmp_path, PBSC_INSTALL_SAMPLE="1", **service_env(stub, tts=False))
    app = create_app(settings)
    client = app.test_client()
    store = app.extensions["pbsc"]["pipeline"].store

    def attach_video(book):      # 示例导入时会清空视频；这里补回片段信息以验证视频依据
        book["video"] = {"path": "video/book.mp4",
                         "clips": [{"video_clip_id": "demo_book_p002", "page_no": 2,
                                    "time_range": [11.04, 22.71]}]}
    store.update("demo_book", attach_video)
    try:
        good = {"answer": "月亮没有真的掉进水里，那是月亮在小河里的影子。", "grounded": True,
                "evidence": [{"page_no": 2, "quote": "乌龟爷爷慢慢地说：“那是月亮的影子。”"}],
                "confidence": 0.9}
        stub.chat_script = [json.dumps(good, ensure_ascii=False)]
        payload = {"book_id": "demo_book", "page_no": 2, "play_time": 12.5, "session_id": "qa-1",
                   "question": "月亮掉到哪里去了？"}
        turn = body_of(client.post(f"{API}/qa/ask", json=payload))["data"]
        assert turn["grounded"] is True and "影子" in turn["answer_text"]
        types = [item["type"] for item in turn["evidence"]]
        assert types == ["page", "video"]
        assert turn["evidence"][0]["quote"] == "乌龟爷爷慢慢地说：“那是月亮的影子。”"
        video = turn["evidence"][1]
        assert video["time_range"][0] <= video["timestamp"] < video["time_range"][1]
        assert set(turn["timings"]) == {"asr_ms", "retrieve_ms", "llm_ms", "tts_ms", "first_audio_ms"}
        assert turn["answer_audio_url"] is None           # 未配置 TTS 时不伪造音频
        prompt = json.dumps(stub.chat_requests[-1], ensure_ascii=False)
        assert "第2页" in prompt and "乌龟爷爷" in prompt

        # 模型说 grounded=true，但依据是编的：按“书里没有”处理
        bad = {"answer": "恐龙住在山洞里。", "grounded": True,
               "evidence": [{"page_no": 2, "quote": "恐龙住在山洞里。"}], "confidence": 0.8}
        stub.chat_script = [json.dumps(bad, ensure_ascii=False)]
        miss = body_of(client.post(f"{API}/qa/ask", json={**payload, "question": "恐龙住在哪里？"}))["data"]
        assert miss["grounded"] is False and miss["evidence"] == [] and "没有讲到" in miss["answer_text"]

        turns = body_of(client.get(f"{API}/history?session_id=qa-1"))["data"]["turns"]
        assert len(turns) == 2 and turns[0]["question_text"] == "月亮掉到哪里去了？"
        assert turns[1]["grounded"] is False
    finally:
        app.extensions["pbsc"]["tasks"].shutdown(wait=True)


def test_history_store_roundtrip_and_session_validation(tmp_path):
    from server.errors import ValidationError
    from server.history_store import HistoryStore, check_session_id
    store = HistoryStore(tmp_path / "history")
    store.add("s-1", {"turn_id": "t1"})
    store.add("s-1", {"turn_id": "t2"})
    assert [t["turn_id"] for t in store.list("s-1")] == ["t1", "t2"]
    assert store.list("missing") == [] and store.sessions() == ["s-1"]
    with pytest.raises(ValidationError):
        check_session_id("bad/id")
