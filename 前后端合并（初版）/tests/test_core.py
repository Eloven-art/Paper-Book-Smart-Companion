# -*- coding: utf-8 -*-
"""通用功能（A 步骤 3）：配置、统一响应、上传校验、任务队列、重试、运行记录、费用台账。"""
from __future__ import annotations

import io
import json
import threading
import time

import pytest
import requests
from werkzeug.datastructures import FileStorage

from agents.cost import CostLedger
from configs.settings import load_dotenv, load_settings, mask_url, redact
from server.errors import (AppError, BudgetExceeded, ExternalServiceError, TaskCancelled, UploadRejected,
                           ValidationError)
from server.retry import call_with_retry
from server.run_logger import RunLogger
from server.task_queue import TaskManager
from server.uploads import sniff, validate_audio, validate_image

from conftest import make_png_bytes, make_wav_bytes

MB = 1024 * 1024


# ---------------------------------------------------------------- 配置
def test_placeholder_means_not_configured():
    s = load_settings({"SIMPLE_STORY_WRITER_URL": "xxxx", "TTS_API_URL": "", "IMAGE2VIDEO_URL": "http://h/x"},
                      use_dotenv=False)
    assert not s.llm.configured and not s.tts.configured and s.i2v.configured


def test_public_summary_never_contains_secrets():
    s = load_settings({"SIMPLE_STORY_WRITER_URL": "http://user:pw@host:9000/v1/chat?token=abc",
                       "SIMPLE_STORY_WRITER_API_KEY": "sk-secret-123"}, use_dotenv=False)
    text = json.dumps(s.public_summary(), ensure_ascii=False)
    assert "sk-secret-123" not in text and "pw" not in text and "abc" not in text
    assert mask_url("http://user:pw@host:9000/v1/chat?token=abc") == "http://host:9000/…"


def test_redact_masks_secret_like_keys():
    assert redact({"api_key": "k", "nested": {"Authorization": "Bearer x", "text": "ok"}}) == \
        {"api_key": "***", "nested": {"Authorization": "***", "text": "ok"}}


def test_dotenv_does_not_override_existing(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text('# 注释\nA=1\nexport B="two"\nC=\n', encoding="utf-8")
    environ = {"A": "keep"}
    assert load_dotenv(env_file, environ) == ["B", "C"]
    assert environ == {"A": "keep", "B": "two", "C": ""}


def test_invalid_number_reports_variable_name():
    with pytest.raises(ValueError, match="PBSC_PORT"):
        load_settings({"PBSC_PORT": "abc"}, use_dotenv=False)


# ---------------------------------------------------------------- 上传校验
def storage(data: bytes, name: str) -> FileStorage:
    return FileStorage(stream=io.BytesIO(data), filename=name)


def test_sniff_known_types():
    assert sniff(make_png_bytes()[:16]) == "png"
    assert sniff(make_wav_bytes()[:16]) == "wav"
    assert sniff(b"\x1a\x45\xdf\xa3" + b"0" * 12) == "webm"
    assert sniff(b"plain text here!") is None


def test_valid_image_accepted():
    up = validate_image(storage(make_png_bytes((640, 360)), "封面.PNG"), max_bytes=MB, max_pixels=10 ** 7)
    assert (up.format, up.ext, up.width, up.height) == ("png", ".png", 640, 360)
    assert len(up.sha256) == 64


@pytest.mark.parametrize("data,name,code", [
    (b"", "a.png", "UPLOAD_EMPTY"),
    (make_png_bytes(), "a.exe", "UPLOAD_TYPE_NOT_ALLOWED"),
    (make_png_bytes(), "a.jpg", "UPLOAD_CONTENT_MISMATCH"),          # 扩展名说是 jpg，内容其实是 png
    (b"<?php echo 1; ?>" * 10, "shell.png", "UPLOAD_CONTENT_MISMATCH"),
    (make_png_bytes()[:60], "broken.png", "UPLOAD_IMAGE_CORRUPT"),
    (make_png_bytes((32, 32)), "tiny.png", "UPLOAD_IMAGE_TOO_SMALL"),
])
def test_bad_images_rejected(data, name, code):
    with pytest.raises(UploadRejected) as err:
        validate_image(storage(data, name), max_bytes=MB, max_pixels=10 ** 7)
    assert err.value.code == code


def test_oversize_and_missing_upload():
    with pytest.raises(UploadRejected) as err:
        validate_image(storage(make_png_bytes((800, 800)), "big.png"), max_bytes=100, max_pixels=10 ** 7)
    assert err.value.code == "UPLOAD_TOO_LARGE" and err.value.http_status == 413
    with pytest.raises(UploadRejected) as err:
        validate_image(storage(make_png_bytes((800, 800)), "big.png"), max_bytes=MB, max_pixels=1000)
    assert err.value.code == "UPLOAD_IMAGE_TOO_LARGE"
    with pytest.raises(UploadRejected) as err:
        validate_audio(None, max_bytes=MB)
    assert err.value.code == "UPLOAD_MISSING"


def test_saved_name_is_server_generated(tmp_path):
    up = validate_image(storage(make_png_bytes(), "../../evil name.png"), max_bytes=MB, max_pixels=10 ** 7)
    path = up.save(tmp_path / "pages", "page_001")
    assert path == tmp_path / "pages" / "page_001.png" and path.is_file()
    assert up.original_name == "evil name.png"


def test_audio_accepted():
    assert validate_audio(storage(make_wav_bytes(), "q.wav"), max_bytes=MB).format == "wav"


# ---------------------------------------------------------------- 重试
def test_retry_recovers_from_transient_errors():
    calls, waits = [], []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise requests.ConnectionError("down")
        return "ok"

    assert call_with_retry(flaky, attempts=3, base_delay=0.5, sleep=waits.append) == "ok"
    assert len(calls) == 3 and len(waits) == 2 and waits[1] > waits[0] * 0.9


def test_retry_gives_up_and_skips_non_retryable():
    calls = []

    def always_500():
        calls.append(1)
        raise ExternalServiceError("boom", service="llm", status=500, retryable=True)

    with pytest.raises(ExternalServiceError):
        call_with_retry(always_500, attempts=3, sleep=lambda s: None)
    assert len(calls) == 3

    calls.clear()

    def bad_request():
        calls.append(1)
        raise ExternalServiceError("bad", service="llm", status=400, retryable=False)

    with pytest.raises(ExternalServiceError):
        call_with_retry(bad_request, attempts=3, sleep=lambda s: None)
    assert len(calls) == 1


# ---------------------------------------------------------------- 任务队列
def test_task_lifecycle_and_persistence(tmp_path):
    tm = TaskManager(workers=1, store_dir=tmp_path)

    def job(ctx):
        ctx.progress(0.5, "一半了", stage="half")
        return {"run_id": "run-1", "value": 42}

    task = tm.submit("demo", job, book_id="b1", page_no=2)
    assert task["status"] == "queued"
    done = tm.wait(task["task_id"], timeout=5)
    assert done["status"] == "succeeded" and done["progress"] == 1.0
    assert done["result"]["value"] == 42 and done["run_id"] == "run-1" and done["stage"] == "half"
    on_disk = json.loads((tmp_path / f"{task['task_id']}.json").read_text(encoding="utf-8"))
    assert on_disk["status"] == "succeeded" and "_persisted_at" not in on_disk
    assert tm.list(book_id="b1")[0]["task_id"] == task["task_id"] and tm.list(book_id="other") == []
    tm.shutdown(wait=True)


def test_task_failures_are_reported(tmp_path):
    tm = TaskManager(workers=2, store_dir=tmp_path)

    def app_error(ctx):
        raise ValidationError("页面不存在", code="PAGE_NOT_FOUND")

    def crash(ctx):
        raise RuntimeError("unexpected")

    a = tm.wait(tm.submit("a", app_error)["task_id"], timeout=5)
    b = tm.wait(tm.submit("b", crash)["task_id"], timeout=5)
    assert a["status"] == "failed" and a["error"]["code"] == "PAGE_NOT_FOUND"
    assert b["status"] == "failed" and b["error"]["code"] == "INTERNAL_ERROR"
    tm.shutdown(wait=True)


def test_task_dedupe_and_cancel(tmp_path):
    tm = TaskManager(workers=1, store_dir=tmp_path)
    gate, started = threading.Event(), threading.Event()

    def slow(ctx):
        started.set()
        gate.wait(5)
        ctx.check_cancelled()
        return {"done": True}

    first = tm.submit("video", slow, dedupe_key="video:b1:1")
    assert started.wait(5)
    again = tm.submit("video", slow, dedupe_key="video:b1:1")
    assert again["task_id"] == first["task_id"] and again["deduplicated"] is True

    queued = tm.submit("video", slow, dedupe_key="video:b1:2")       # 只有一个工作线程，这个在排队
    assert tm.cancel(queued["task_id"])["status"] == "cancelled"
    tm.cancel(first["task_id"])                                       # 执行中的任务：置取消标记
    gate.set()
    assert tm.wait(first["task_id"], timeout=5)["status"] == "cancelled"
    assert tm.cancel("task-nope") is None
    tm.shutdown(wait=True)


def test_unfinished_tasks_marked_interrupted_after_restart(tmp_path):
    (tmp_path / "task-old.json").write_text(json.dumps(
        {"task_id": "task-old", "kind": "video", "status": "running", "created_at": "2026-11-10T10:00:00",
         "book_id": "b1", "page_no": None}), encoding="utf-8")
    tm = TaskManager(workers=1, store_dir=tmp_path)
    old = tm.get("task-old")
    assert old["status"] == "failed" and old["error"]["code"] == "INTERRUPTED"
    tm.shutdown(wait=True)


# ---------------------------------------------------------------- 运行记录
def test_run_record_ok_and_error(tmp_path):
    logger = RunLogger(tmp_path / "runs")
    out = tmp_path / "out.txt"
    out.write_text("hello", encoding="utf-8")
    with logger.start("narration", {"book_id": "b1", "page_no": 3, "api_key": "should-hide"}) as run:
        run.set_prompt("narration_v1", "abc123", rendered=[{"role": "user", "content": "x"}])
        run.set_model("deepseek-v3.2", {"temperature": 0.3, "token": "hide-me"}, provider="openai_compatible_chat")
        run.add_output(out, "text")
        run.set_cost({"amount": None})
    record = logger.find(run.run_id)
    assert record["status"] == "ok" and record["elapsed_ms"] >= 0 and record["finished_at"]
    assert record["input"] == {"book_id": "b1", "page_no": 3, "api_key": "***"}
    assert record["prompt"]["version"] == "narration_v1" and record["model"]["params"]["token"] == "***"
    assert record["outputs"][0]["bytes"] == 5 and len(record["outputs"][0]["sha256"]) == 64

    with pytest.raises(ExternalServiceError):
        with logger.start("i2v", {"book_id": "b1"}, parent_run_id=run.run_id) as failed:
            raise ExternalServiceError("网关超时", service="i2v", retryable=True)
    record = logger.find(failed.run_id)
    assert record["status"] == "error" and record["error"]["code"] == "EXTERNAL_SERVICE_ERROR"
    assert record["parent_run_id"] == run.run_id and "网关超时" in record["error"]["traceback"]

    index = [json.loads(l) for l in (tmp_path / "runs" / "index.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [r["status"] for r in index] == ["ok", "error"] and index[0]["page_no"] == 3
    assert logger.find("no-such-run") is None


# ---------------------------------------------------------------- 费用台账
def test_ledger_prices_and_budget(tmp_path):
    pricing = tmp_path / "pricing.json"
    pricing.write_text(json.dumps({
        "currency": "CNY",
        "services": {"i2v": {"*": {"per_second": 0.2, "per_call": None}},
                     "llm": {"*": {"per_1k_input_tokens": 0.002, "per_1k_output_tokens": None}}},
        "budgets": {"i2v": {"max_calls_per_day": 2, "max_amount_total": None}}}), encoding="utf-8")
    ledger = CostLedger(tmp_path / "ledger.jsonl", pricing)
    assert ledger.estimate("i2v", "any", {"seconds": 5, "calls": 1}) == 1.0
    assert ledger.estimate("llm", "m", {"prompt_tokens": 1000, "completion_tokens": 10}) is None   # 缺单价不乱算
    assert ledger.estimate("tts", "m", {"chars": 100}) is None

    ledger.check("i2v")
    ledger.record("i2v", "m", {"seconds": 5, "calls": 1}, run_id="r1")
    ledger.record("i2v", None, {"seconds": 5, "calls": 1}, run_id="r2", billable=False)   # 本地降级不计入预算
    ledger.check("i2v")
    ledger.record("i2v", "m", {"seconds": 5, "calls": 1}, run_id="r3")
    with pytest.raises(BudgetExceeded):
        ledger.check("i2v")
    summary = ledger.summary()["i2v"]
    assert summary == {"calls": 3, "billable_calls": 2, "amount": 2.0, "unpriced_calls": 0,
                       "units": {"seconds": 15, "calls": 3}}
    ledger.check("llm")       # 没设预算的服务不限制


def test_ledger_total_amount_budget(tmp_path):
    pricing = tmp_path / "pricing.json"
    pricing.write_text(json.dumps({"services": {"i2v": {"*": {"per_call": 3.0}}},
                                   "budgets": {"i2v": {"max_calls_per_day": None, "max_amount_total": 5.0}}}),
                       encoding="utf-8")
    ledger = CostLedger(tmp_path / "ledger.jsonl", pricing)
    ledger.record("i2v", "m", {"calls": 1})
    ledger.check("i2v")
    ledger.record("i2v", "m", {"calls": 1})
    with pytest.raises(BudgetExceeded) as err:
        ledger.check("i2v")
    assert err.value.details["limit"] == "max_amount_total"


def test_error_classes_carry_codes():
    assert AppError("x").to_dict() == {"code": "INTERNAL_ERROR", "message": "x"}
    assert TaskCancelled("x").http_status == 409
    e = ExternalServiceError("x", service="tts", status=503, retryable=True)
    assert e.retryable and e.details == {"service": "tts", "upstream_status": 503}
