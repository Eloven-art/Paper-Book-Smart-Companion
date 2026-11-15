# -*- coding: utf-8 -*-
"""OCR 节点测试：用假引擎验证结果整理、写回、缓存和接口行为，不加载真实模型。

真实模型的识别质量在 `python scripts/download_ocr_models.py` 和手工验收里检查；
这里只证明“RapidOCR 的输出如何变成接口约定的 ocr_text”，避免测试依赖网络或 30 MB 模型。
"""
import io
import time

from PIL import Image

from agents.ocr_agent import normalize_result

API = "/api/v1"


def png_bytes(size=(640, 360), color=(230, 230, 230)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


class FakeResult:
    def __init__(self, items):
        self.boxes = [item[0] for item in items]
        self.txts = tuple(item[1] for item in items)
        self.scores = tuple(item[2] for item in items)


class FakeEngine:
    """返回固定的两行文字，记录被调用次数。"""

    def __init__(self):
        self.calls = []

    def __call__(self, path):
        self.calls.append(str(path))
        return FakeResult([
            ([[50, 10], [250, 10], [250, 40], [50, 40]], "第一行文字", 0.99),
            ([[200, 60], [420, 60], [420, 90], [200, 90]], "第二行文字", 0.95),
            ([[300, 10], [330, 40], [330, 40], [300, 10]], "★", 0.99),
            ([[10, 10], [30, 30], [30, 30], [10, 10]], "_", 0.90),
        ])


def wait_task(client, task_id: str, timeout: float = 15.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        task = client.get(f"{API}/tasks/{task_id}").json["data"]
        if task["status"] in ("succeeded", "failed", "cancelled"):
            return task
        time.sleep(0.05)
    raise AssertionError(f"任务 {task_id} 超时未结束")


def test_normalize_result_orders_lines_and_drops_symbols():
    out = normalize_result(FakeEngine()("unused.png"))
    assert out["full_text"] == "第一行文字\n第二行文字"
    assert [b["text"] for b in out["blocks"]] == ["第一行文字", "第二行文字"]
    assert out["blocks"][0]["bbox"] == [50, 10, 250, 40]
    assert out["blocks"][0]["score"] == 0.99


def test_normalize_result_repairs_trailing_quote_confusion():
    result = FakeResult([([[10, 10], [400, 40], [400, 70], [10, 70]],
                          "乌龟爷爷慢慢地说：“那是月亮的影子。99", 0.99)])
    out = normalize_result(result)
    assert out["full_text"].endswith("。”")


def test_normalize_result_repairs_separate_quote_block():
    result = FakeResult([
        ([[10, 10], [400, 40], [400, 70], [10, 70]], "乌龟爷爷慢慢地说：“那是月亮的影子。", 0.99),
        ([[410, 20], [440, 20], [440, 40], [410, 40]], "99", 0.97),
    ])
    out = normalize_result(result)
    assert out["full_text"].endswith("。”")
    assert out["blocks"][-1]["text"] == "”"


def test_ocr_status_exposes_deployment_state(app):
    status = app.extensions["pbsc"]["pipeline"].ocr.status()
    for key in ("engine", "model_dir", "available", "vendor_ready", "missing_dependencies",
                "models_ready", "loaded"):
        assert key in status
    assert status["engine"] == "onnxruntime"
    assert status["vendor_ready"] is True


def test_ocr_endpoint_writes_pages_and_reuses_cache(app, client):
    resp = client.post(f"{API}/books", data={
        "book_id": "ocr_test", "title": "OCR 测试",
        "pages": [(io.BytesIO(png_bytes()), "1.png"), (io.BytesIO(png_bytes()), "2.png")],
    }, content_type="multipart/form-data")
    assert resp.status_code == 201

    engine = FakeEngine()
    app.extensions["pbsc"]["pipeline"].ocr._engine = engine

    submitted = client.post(f"{API}/books/ocr_test/ocr", json={}).json["data"]
    done = wait_task(client, submitted["task_id"])
    assert done["status"] == "succeeded", done
    assert done["result"]["succeeded"] == 2 and done["result"]["failed"] == 0

    page = client.get(f"{API}/books/ocr_test/pages/1").json["data"]
    assert page["ocr_text"]["full_text"] == "第一行文字\n第二行文字"
    assert page["ocr_text"]["blocks"][0]["bbox"] == [50, 10, 250, 40]
    assert page["ocr_meta"]["run_id"] and page["ocr_meta"]["block_count"] == 2
    assert page["safety_check"]["status"] == "pending_review"
    assert len(engine.calls) == 2

    again = client.post(f"{API}/books/ocr_test/ocr", json={}).json["data"]
    cached = wait_task(client, again["task_id"])
    assert cached["status"] == "succeeded" and cached["result"]["cached"] == 2
    assert len(engine.calls) == 2                      # 已有结果不再识别

    forced = client.post(f"{API}/books/ocr_test/ocr", json={"page_no": 1, "force": True}).json["data"]
    redone = wait_task(client, forced["task_id"])
    assert redone["status"] == "succeeded" and redone["result"]["cached"] is False
    assert len(engine.calls) == 3


def test_upload_can_start_ocr_task(app, client):
    engine = FakeEngine()
    app.extensions["pbsc"]["pipeline"].ocr._engine = engine
    resp = client.post(f"{API}/books", data={
        "book_id": "auto_ocr", "ocr": "1",
        "pages": [(io.BytesIO(png_bytes()), "1.png")],
    }, content_type="multipart/form-data")
    assert resp.status_code == 201
    created = resp.json["data"]
    assert created["task_id"]
    done = wait_task(client, created["task_id"])
    assert done["status"] == "succeeded"
    page = client.get(f"{API}/books/auto_ocr/pages/1").json["data"]
    assert page["ocr_text"]["full_text"] == "第一行文字\n第二行文字"


def test_ocr_unavailable_returns_explicit_error(app, client, monkeypatch):
    resp = client.post(f"{API}/books", data={
        "book_id": "no_ocr", "pages": [(io.BytesIO(png_bytes()), "1.png")],
    }, content_type="multipart/form-data")
    assert resp.status_code == 201

    from agents.ocr_agent import OCRAgent
    monkeypatch.setattr(OCRAgent, "status", lambda self: {
        "engine": "onnxruntime", "model_dir": "missing", "auto": False, "available": False,
        "vendor_ready": False, "missing_dependencies": ["onnxruntime"], "models_ready": False,
        "model_files": [], "loaded": False, "error": None,
    })
    resp = client.post(f"{API}/books/no_ocr/ocr", json={})
    assert resp.status_code == 503
    assert resp.json["error"]["code"] == "OCR_UNAVAILABLE"
