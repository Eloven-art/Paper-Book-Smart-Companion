# -*- coding: utf-8 -*-
"""旁白生成（A 步骤 4）：检查规则、模型输出解析、重试与降级、写回分页数据。"""
from __future__ import annotations

import json

import pytest

from agents.pipeline import build_pipeline
from agents.story_agent import (clean_text, coverage_ratio, load_prompt, parse_model_output, passthrough_narration,
                                support_ratio, validate_narration)

from conftest import make_settings, service_env

GOOD_P1 = "夜晚，草地上静悄悄的。小兔子朵朵抬起头，看见了一个圆圆的月亮。朵朵想：月亮住在哪里呢？"
GOOD_P2 = "朵朵走呀走，来到了小河边。咦，月亮怎么掉进水里啦？乌龟爷爷慢慢地说：别着急，那是月亮的影子。"
GOOD_P3 = "后来，朵朵和乌龟爷爷一起坐在山坡上看月亮。原来呀，月亮一直在天上，陪着大家呢。"
INVENTED = "从前有一只勇敢的小狐狸，它骑着飞毯去了遥远的沙漠王国，在那里打败了喷火的恶龙，救出了公主。"


def reply(text: str) -> str:
    return json.dumps({"narration": text}, ensure_ascii=False)


@pytest.fixture
def book(sample_dir):
    return json.loads((sample_dir / "book.json").read_text(encoding="utf-8"))


def codes(issues) -> list:
    return [i["code"] for i in issues]


# ---------------------------------------------------------------- 规则与解析
def test_prompt_file_has_version_and_sections(settings):
    prompt = load_prompt(settings.prompts_dir, "narration_v1")
    assert prompt.version == "narration_v1" and len(prompt.sha256) == 64
    assert "讲述原书" in prompt.sections["system"] and "$ocr_text" in prompt.sections["user"]
    assert not prompt.sections["system"].startswith("#")


@pytest.mark.parametrize("raw,expected", [
    ('{"narration": "朵朵看见了月亮。"}', "朵朵看见了月亮。"),
    ('```json\n{"narration": "朵朵看见了月亮。"}\n```', "朵朵看见了月亮。"),
    ('好的，以下是旁白：{"narration": "朵朵看见了月亮。"} 希望有帮助', "朵朵看见了月亮。"),
    ("旁白：朵朵看见了月亮。\n她很开心。", "朵朵看见了月亮。她很开心。"),
    ("“朵朵看见了月亮。”", "朵朵看见了月亮。"),
])
def test_parse_model_output(raw, expected):
    assert parse_model_output(raw) == expected


def test_good_narration_passes(book):
    check = validate_narration(GOOD_P1, book["pages"][0], book)
    assert check.ok and not check.soft
    assert check.metrics["support"] >= 0.5 and check.metrics["coverage"] >= 0.5


def test_original_text_passes_as_its_own_narration(book):
    for page in book["pages"]:
        check = validate_narration(passthrough_narration(page), page, book)
        assert check.ok and check.metrics["support"] == 1.0 and check.metrics["coverage"] == 1.0


def test_invented_story_is_rejected(book):
    check = validate_narration(INVENTED, book["pages"][0], book)
    assert not check.ok
    assert "missing_character" in codes(check.hard) and "unsupported_content" in codes(check.hard)


def test_dropped_character_markup_length_interaction(book):
    page2 = book["pages"][1]
    no_turtle = "朵朵走到小河边，发现月亮掉进了水里。有人慢慢地说：那是月亮的影子。"
    assert "missing_character" in codes(validate_narration(no_turtle, page2, book).hard)
    assert "markup" in codes(validate_narration(GOOD_P2 + "<SFX>水声</SFX>", page2, book).hard)
    assert "too_long" in codes(validate_narration(GOOD_P2 * 4, page2, book).hard)
    assert "too_short" in codes(validate_narration("朵朵和乌龟爷爷。", page2, book).hard)
    assert "empty" in codes(validate_narration("  ", page2, book).hard)
    chatty = GOOD_P2 + "小朋友，你知道为什么吗？"
    assert "reader_interaction" in codes(validate_narration(chatty, page2, book).soft)


def test_alias_instead_of_canonical_name_is_soft_issue(book):
    renamed = "夜晚的草地上，小兔子抬头看见了圆圆的月亮。月亮住在哪里呢？小兔子想。"
    check = validate_narration(renamed, book["pages"][0], book)
    assert check.ok and "name_changed" in codes(check.soft)


def test_ratio_helpers():
    assert clean_text(" 朵朵 \n 想 ") == "朵朵想"
    assert support_ratio("朵朵看月亮", ["朵朵看月亮"]) == 1.0
    assert support_ratio("飞毯沙漠恶龙", ["朵朵看月亮"]) == 0.0
    assert coverage_ratio("", "任何内容") == 1.0


# ---------------------------------------------------------------- 节点
def pipeline_with_sample(tmp_path, sample_dir, **env):
    pipe = build_pipeline(make_settings(tmp_path, **env))
    pipe.store.install_sample(sample_dir)
    return pipe


def test_passthrough_when_llm_not_configured(tmp_path, sample_dir):
    pipe = pipeline_with_sample(tmp_path, sample_dir)
    result = pipe.narration.generate_book("demo_book")
    book = pipe.store.load("demo_book")
    for page, summary in zip(book["pages"], result["pages"]):
        assert summary["method"] == "passthrough_ocr" and summary["needs_review"]
        assert page["narration"] == page["ocr_text"]["full_text"]
        assert page["narration_meta"]["fallback_reason"].startswith("大模型未配置")
        assert page["safety_check"]["status"] == "pending_review"
    assert pipe.ledger.entries() == []                       # 没有调用任何收费服务


def test_llm_narration_written_back_with_trace(tmp_path, sample_dir, stub):
    stub.chat_script = [reply(GOOD_P1), reply(GOOD_P2), reply(GOOD_P3)]
    pipe = pipeline_with_sample(tmp_path, sample_dir, **service_env(stub, tts=False, i2v=False))
    result = pipe.narration.generate_book("demo_book")
    book = pipe.store.load("demo_book")
    assert [p["narration"] for p in book["pages"]] == [GOOD_P1, GOOD_P2, GOOD_P3]
    assert all(p["method"] == "llm" and not p["needs_review"] for p in result["pages"])

    # 请求形状与参考代码一致：model / temperature / stop / messages，带 Bearer 密钥
    first = stub.chat_requests[0]
    assert first["model"] == "stub-llm" and first["temperature"] == 0.3 and first["messages"][0]["role"] == "system"
    assert "朵朵" in first["messages"][1]["content"] and "（这是第一页）" in first["messages"][1]["content"]
    assert GOOD_P1 in stub.chat_requests[1]["messages"][1]["content"]        # 第 2 页带上了第 1 页的旁白

    # 运行记录能回答“这段旁白是怎么来的”
    meta = book["pages"][1]["narration_meta"]
    record = pipe.runs.find(meta["run_id"])
    assert record["kind"] == "narration" and record["parent_run_id"] == result["run_id"]
    assert record["prompt"]["version"] == "narration_v1" and record["model"]["name"] == "stub-llm"
    assert record["input"]["page_no"] == 2 and record["notes"]["result"]["narration"] == GOOD_P2
    assert "test-key" not in json.dumps(record, ensure_ascii=False)

    # 旁白变了：安全复核回到待复核；费用台账记了 3 次调用的 token 用量
    assert book["pages"][0]["safety_check"]["status"] == "pending_review"
    entries = pipe.ledger.entries()
    assert len(entries) == 3 and entries[0]["units"]["prompt_tokens"] == 100 and entries[0]["amount"] is None


def test_bad_output_retried_with_feedback(tmp_path, sample_dir, stub):
    stub.chat_script = [reply(INVENTED), reply(GOOD_P1)]
    pipe = pipeline_with_sample(tmp_path, sample_dir, **service_env(stub, tts=False, i2v=False))
    result = pipe.narration.generate_book("demo_book", pages=[1])
    assert result["pages"][0]["narration"] == GOOD_P1 and result["pages"][0]["method"] == "llm"
    retry_prompt = stub.chat_requests[1]["messages"][1]["content"]
    assert "没有通过检查" in retry_prompt and "小狐狸" in retry_prompt
    meta = pipe.store.load("demo_book")["pages"][0]["narration_meta"]
    assert meta["attempts"] == 2


def test_falls_back_to_original_text_when_model_keeps_inventing(tmp_path, sample_dir, stub):
    stub.chat_script = [reply(INVENTED)] * 3
    pipe = pipeline_with_sample(tmp_path, sample_dir, **service_env(stub, tts=False, i2v=False))
    result = pipe.narration.generate_book("demo_book", pages=[1])
    page = pipe.store.load("demo_book")["pages"][0]
    assert result["pages"][0]["method"] == "fallback_passthrough" and result["pages"][0]["needs_review"]
    assert page["narration"] == page["ocr_text"]["full_text"]
    assert len(stub.chat_requests) == 3
    assert "小狐狸" not in page["narration"]


def test_transient_llm_errors_are_retried_then_fall_back(tmp_path, sample_dir, stub):
    stub.chat_script = [503, 503, reply(GOOD_P1)]
    pipe = pipeline_with_sample(tmp_path, sample_dir, **service_env(stub, tts=False, i2v=False))
    assert pipe.narration.generate_book("demo_book", pages=[1])["pages"][0]["method"] == "llm"
    assert len(stub.chat_requests) == 3

    stub.chat_script = [500, 500, 500]
    result = pipe.narration.generate_book("demo_book", pages=[2])
    meta = pipe.store.load("demo_book")["pages"][1]["narration_meta"]
    assert result["pages"][0]["method"] == "fallback_passthrough"
    assert "EXTERNAL_SERVICE_ERROR" in meta["fallback_reason"]


def test_unknown_page_raises_not_found(tmp_path, sample_dir):
    from server.errors import NotFoundError
    pipe = pipeline_with_sample(tmp_path, sample_dir)
    with pytest.raises(NotFoundError) as err:
        pipe.narration.generate_book("demo_book", pages=[99])
    assert err.value.code == "PAGE_NOT_FOUND"
