# -*- coding: utf-8 -*-
"""画面理解的纯函数测试：坐标统一、JSON 解析、物体框过滤。

这里的样例框来自真机跑 glm-4.6v-flash 拿到的返回（demo_book 第 1 页，1280x720），
所以能覆盖真实会遇到的两种毛病：下边越界、以及把整页当成一个物体。
"""
from __future__ import annotations

from agents.understanding import normalize_bbox, normalize_objects, parse_json

W, H = 1280, 720


def test_bbox_is_clamped_into_frame():
    # 真机返回：下边 726 / 734 都超出了 720
    assert normalize_bbox([33, 213, 261, 726], W, H) == [33, 213, 261, 720]
    assert normalize_bbox([436, 360, 504, 734], W, H) == [436, 360, 504, 720]
    assert normalize_bbox([690, 67, 841, 347], W, H) == [690, 67, 841, 347]


def test_bbox_handles_normalized_and_junk():
    assert normalize_bbox([0.1, 0.2, 0.5, 0.6], W, H) == [128, 144, 640, 432]
    assert normalize_bbox([10, 10, 10, 10], W, H) is None      # 零面积
    assert normalize_bbox([1, 2, 3], W, H) is None             # 不是 4 个数
    assert normalize_bbox("abc", W, H) is None


def test_parse_json_survives_code_fence():
    assert parse_json('```json\n{"objects": []}\n```') == {"objects": []}
    assert parse_json('好的，结果如下：\n{"full_text": "雪"}\n希望有用') == {"full_text": "雪"}


def test_parse_json_repairs_trailing_comma():
    # 真机上第 2 页就是这么挂的：数组末尾多一个逗号，json.loads 报
    # "Expecting ',' delimiter: line 11 column 2"
    assert parse_json('{"objects": [{"name": "兔子", "bbox": [1, 2, 30, 40]},],}')["objects"][0]["name"] == "兔子"
    assert parse_json('{"objects": [],}') == {"objects": []}


def test_normalize_objects_drops_whole_page_and_junk():
    raw = [
        {"name": "兔子", "bbox": [436, 360, 504, 734]},      # 越界 → 裁剪后保留
        {"name": "背景", "bbox": [0, 0, 1280, 720]},          # 整页 → 丢掉
        {"name": "", "bbox": [10, 10, 100, 100]},             # 没名字 → 丢掉
        {"name": "月亮", "bbox": [1, 1, 2, 2]},               # 太小 → 丢掉
    ]
    objects, adjusted = normalize_objects(raw, W, H)
    assert [o["name"] for o in objects] == ["兔子"]
    assert objects[0]["bbox"] == [436, 360, 504, 720]
    assert adjusted == 4        # 兔子被裁剪，另外三条被丢弃
