# -*- coding: utf-8 -*-
"""VLM 接入测试：结果归一化、bbox 校验/缩放、写回分页数据、接口任务和失败提示。

用假 VLM client 验证协议与写回，不依赖同学的真实服务；真实收发用 scripts/generate_book.py
--steps vlm 和 /api/v1/books/<id>/vlm 在本机对同学服务验收。
"""
import json
import time

import pytest
from conftest import make_settings

from agents.pipeline import build_pipeline
from agents.vlm_agent import normalize_vlm_result
from agents.vlm_client import VLMClient, VLMResult
from server.app import create_app
from server.errors import VLMFailed

API = "/api/v1"
SAMPLE = "demo_book"


class FakeVLMClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def analyze(self, image_data_uri, system_prompt, user_prompt, *, page_no, image_size):
        self.calls.append({"page_no": page_no, "image_size": image_size,
                           "system": system_prompt, "user": user_prompt,
                           "image": image_data_uri[:30]})
        return VLMResult(json.dumps(self.payload, ensure_ascii=False), "fake-vlm", 5)


def base_payload(**overrides):
    payload = {
        "page_no": 1,
        "scene_summary": "夜晚的草地上，小兔子看着圆圆的月亮。",
        "objects": [
            {"name": "月亮", "bbox": [880, 60, 1060, 230], "explanation": "天上圆圆的月亮。", "confidence": 0.92},
            {"name": "兔子", "bbox": [560, 300, 700, 620], "explanation": "小兔子朵朵。"},
        ],
        "plot_events": ["朵朵抬头看月亮"],
    }
    payload.update(overrides)
    return payload


class FakeResponse:
    def __init__(self, body):
        self.status_code = 200
        self._body = body
        self.text = json.dumps(body, ensure_ascii=False)

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, body):
        self.body = body
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return FakeResponse(self.body)


def test_vlm_client_openai_chat_sends_image_and_reads_content():
    session = FakeSession({"model": "fake-vlm",
                           "choices": [{"message": {"role": "assistant", "content": '{"page_no":1}'}}],
                           "usage": {"prompt_tokens": 10, "completion_tokens": 5}})
    client = VLMClient("http://vlm/v1/chat/completions", "test-key", "fake-vlm", session=session)
    result = client.analyze("data:image/jpeg;base64,abc", "system", "user", page_no=1,
                            image_size={"width": 100, "height": 50})
    assert result.text == '{"page_no":1}' and result.usage["prompt_tokens"] == 10
    payload = json.loads(session.calls[0][1]["data"])
    assert payload["messages"][1]["content"][1]["image_url"]["url"].startswith("data:image/jpeg")


def test_vlm_client_custom_json_unwraps_result():
    session = FakeSession({"result": {"page_no": 1, "scene_summary": "画面", "objects": []}})
    client = VLMClient("http://vlm/analyze", None, "fake-vlm", protocol="custom_json", session=session)
    result = client.analyze("data:image/jpeg;base64,abc", "system", "user", page_no=1,
                            image_size={"width": 100, "height": 50})
    body = json.loads(result.text)
    assert body["page_no"] == 1 and body["scene_summary"] == "画面"
    payload = json.loads(session.calls[0][1]["data"])
    assert payload["page_no"] == 1 and payload["image_size"] == {"width": 100, "height": 50}


def test_normalize_scales_bbox_and_keeps_real_contours():
    payload = base_payload(objects=[
        {"name": "月亮", "bbox": [440, 30, 530, 115],
         "contours": [[[440, 30], [530, 30], [530, 115], [440, 115]]]},
        {"name": "兔子", "bbox": [280, 150, 350, 310]},
    ])
    result = normalize_vlm_result(payload, expected_page_no=1,
                                  sent_size=(640, 360), original_size=(1280, 720))
    assert result["page_no"] == 1
    assert result["objects"][0]["bbox"] == [880, 60, 1060, 230]
    assert result["objects"][0]["contours"][0][0] == [880, 60]
    assert "contours" not in result["objects"][1]       # 没有真实轮廓就不伪造
    assert result["plot_events"] == ["朵朵抬头看月亮"]


def test_normalize_xywh_and_drops_invalid_boxes():
    payload = base_payload(objects=[
        {"name": "树", "bbox": [10, 10, 40, 30]},        # xywh
        {"name": "坏框", "bbox": [1, 2]},
        {"name": "", "bbox": [1, 2, 3, 4]},
    ])
    result = normalize_vlm_result(payload, expected_page_no=1,
                                  sent_size=(100, 100), original_size=(200, 200),
                                  bbox_format="xywh")
    assert result["objects"][0]["bbox"] == [20, 20, 100, 80]
    assert len(result["objects"]) == 1
    assert any(w.startswith("dropped_objects:") for w in result["warnings"])


def test_normalize_classmate_dict_and_1000_coordinates():
    """兼容赛题参考代码 gemini_analyze.py 的 {'名称<English>': [x1,y1,x2,y2]} 格式。"""
    payload = {
        "月亮<Moon>": [500, 100, 800, 300],
        "兔子<Rabbit>": [100, 400, 300, 800],
    }
    result = normalize_vlm_result(payload, expected_page_no=1,
                                  sent_size=(1000, 1000), original_size=(2000, 2000),
                                  bbox_format="xyxy_1000")
    assert [o["name"] for o in result["objects"]] == ["月亮", "兔子"]
    assert result["objects"][0]["bbox"] == [1000, 200, 1600, 600]
    assert "scene_summary_missing" in result["warnings"]
    assert "page_no_missing" in result["warnings"]


def test_normalize_rejects_wrong_page():
    with pytest.raises(VLMFailed):
        normalize_vlm_result(base_payload(page_no=2), expected_page_no=1,
                             sent_size=(640, 360), original_size=(1280, 720))


def test_vlm_agent_writes_page_and_caches(tmp_path, sample_dir):
    settings = make_settings(tmp_path, VLM_API_URL="http://127.0.0.1:9999/v1/chat/completions",
                             VLM_MODEL="fake-vlm")
    pipe = build_pipeline(settings)
    pipe.store.install_sample(sample_dir)
    client = FakeVLMClient(base_payload())
    pipe.vlm._client = client

    first = pipe.vlm.analyze_page(SAMPLE, 1)
    assert first["cached"] is False and first["object_count"] == 2
    page = pipe.store.load(SAMPLE)["pages"][0]
    assert page["scene_summary"] == "夜晚的草地上，小兔子看着圆圆的月亮。"
    assert [o["name"] for o in page["objects"]] == ["月亮", "兔子"]
    assert page["objects"][0]["bbox"] == [880, 60, 1060, 230]
    assert page["vlm_meta"]["run_id"] == first["run_id"]
    assert page["vlm_meta"]["image_sha256"]
    assert page["safety_check"]["status"] == "pending_review"

    second = pipe.vlm.analyze_page(SAMPLE, 1)
    assert second["cached"] is True and len(client.calls) == 1


def test_vlm_endpoint_task_and_unavailable(tmp_path, sample_dir, monkeypatch):
    settings = make_settings(tmp_path, VLM_API_URL="http://127.0.0.1:9999/v1/chat/completions",
                             VLM_MODEL="fake-vlm")
    app = create_app(settings)
    try:
        pipeline = app.extensions["pbsc"]["pipeline"]
        pipeline.store.install_sample(sample_dir)
        pipeline.vlm._client = FakeVLMClient(base_payload())
        client = app.test_client()
        submitted = client.post(f"{API}/books/{SAMPLE}/vlm", json={"page_no": 1}).json["data"]
        assert submitted["kind"] == "vlm" and submitted["task_id"]
        deadline = time.time() + 10
        while time.time() < deadline:
            task = client.get(f"{API}/tasks/{submitted['task_id']}").json["data"]
            if task["status"] in ("succeeded", "failed"):
                break
            time.sleep(0.05)
        assert task["status"] == "succeeded", task
        page = client.get(f"{API}/books/{SAMPLE}/pages/1").json["data"]
        assert page["objects"][0]["name"] == "月亮"
        assert page["vlm_meta"]["object_count"] == 2

        from agents.vlm_agent import VLMAgent
        monkeypatch.setattr(VLMAgent, "status", lambda self: {
            "configured": False, "available": False, "url_configured": False,
            "model": None, "protocol": "openai_chat", "auto": False, "prompt_version": "vlm_v1"})
        resp = client.post(f"{API}/books/{SAMPLE}/vlm", json={"page_no": 1})
        assert resp.status_code == 503 and resp.json["error"]["code"] == "VLM_UNAVAILABLE"
    finally:
        app.extensions["pbsc"]["tasks"].shutdown(wait=True)
