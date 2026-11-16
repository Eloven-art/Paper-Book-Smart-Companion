"""Video frame embeddings and short-range tracking for the reading tools."""
import math
import os

import requests


def embed(texts, ledger=None):
    url = os.getenv("PBSC_EMBEDDINGS_URL","").strip()
    if not url:
        return None
    model = os.getenv("PBSC_EMBEDDINGS_MODEL","").strip()
    if not model:
        raise ValueError("PBSC_EMBEDDINGS_MODEL is required with PBSC_EMBEDDINGS_URL")
    key = os.getenv("PBSC_EMBEDDINGS_KEY")
    def request():
        response = requests.post(url, headers={"Authorization": f"Bearer {key}"} if key else {},
                                 json={"model": model, "input": texts}, timeout=10)
        response.raise_for_status()
        return response
    if ledger is None:
        response = request()
    else:
        with ledger.reserve("embedding", model, {"calls": 1}):
            response = request()
            ledger.record("embedding", model, {"calls": 1}, note="视频语义检索")
    vectors = [row["embedding"] for row in sorted(response.json()["data"], key=lambda row: row["index"])]
    if len(vectors) != len(texts):
        raise ValueError("Embedding response count mismatch")
    return vectors


def cosine(left, right):
    if not left or not right or len(left) != len(right):
        return 0
    scale = math.hypot(*left) * math.hypot(*right)
    return sum(a * b for a, b in zip(left, right)) / scale if scale else 0


def track(previous, current, objects):
    import cv2
    height, width = current.shape
    result = []
    for item in objects:
        x, y, right, bottom = map(round, item["bbox"])
        w, h = right - x, bottom - y
        if w < 8 or h < 8:
            continue
        patch = previous[y:bottom, x:right]
        if patch.std() < 5:
            continue
        dx, dy = max(12, w // 3), max(12, h // 3)
        sx, sy = max(0, x-dx), max(0, y-dy)
        region = current[sy:min(height, bottom+dy), sx:min(width, right+dx)]
        scores = cv2.matchTemplate(region, patch, cv2.TM_CCOEFF_NORMED)
        _, confidence, _, (nx, ny) = cv2.minMaxLoc(scores)
        if confidence >= 0.82:
            result.append({**item, "bbox": [sx+nx, sy+ny, sx+nx+w, sy+ny+h]})
    return result
