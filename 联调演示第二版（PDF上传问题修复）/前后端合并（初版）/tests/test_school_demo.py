from types import SimpleNamespace

import numpy as np

from agents.content_review import screen_answer
from agents.video_index import cosine, track


def test_child_screen_rejects_risk_and_unknown(monkeypatch):
    monkeypatch.setenv("PBSC_CHILD_REVIEW","1")
    chat = SimpleNamespace(chat=lambda *a, **kw: SimpleNamespace(text="PASS"))
    assert screen_answer(chat,"月亮在哪里","月亮在天上")
    assert not screen_answer(chat,"问月亮","这是家庭住址")
    unavailable = SimpleNamespace(chat=lambda *a, **kw: SimpleNamespace(text="无法判断"))
    assert not screen_answer(unavailable,"问月亮","月亮在天上")


def test_tracker_moves_box_and_semantic_cosine():
    previous = np.zeros((110,110),dtype=np.uint8)
    patch = np.random.default_rng(5).integers(0,255,(18,18),dtype=np.uint8)
    previous[30:48,30:48] = patch
    current = np.zeros_like(previous)
    current[33:51,35:53] = patch
    items = track(previous,current,[{"bbox":[30,30,48,48],"name":"兔"}])
    assert items[0]["bbox"] == [35,33,53,51]
    assert cosine([1,0],[1,0]) == 1
    assert cosine([1,0],[0,1]) == 0


def test_video_quote_maps_to_real_frame_timestamp(tmp_path):
    from conftest import make_settings
    from agents.pipeline import build_pipeline
    agent = build_pipeline(make_settings(tmp_path)).qa
    book = {"pages":[{"page_no":1,"keyframes":[{"scene_summary":"小屋的窗户上出现一颗金星","time":1.75}]}],
            "video":{"path":"video/book.mp4"}}
    chosen = agent.retrieve(book,1,"窗户上出现什么金星")
    assert chosen[0][1][0].frame["time"] == 1.75
    evidence = agent._validate_evidence(book,[{"page_no":1,"quote":"窗户上出现一颗金星"}])
    assert agent._decorate_evidence(book,evidence,lambda path:path)[-1]["timestamp"] == 1.75


def test_pending_media_requires_approval_bound_to_content(monkeypatch,tmp_path):
    import json
    from conftest import make_settings
    from agents.content_review import fingerprint, review_path
    from server.app import create_app

    monkeypatch.setenv("PBSC_CHILD_REVIEW","1")
    settings = make_settings(tmp_path)
    app = create_app(settings)
    store = app.extensions["pbsc"]["pipeline"].store
    from pathlib import Path
    from shutil import copytree
    copytree(Path("data/samples/demo_book"),store.book_dir("demo_book"))
    book = store.load("demo_book")
    video = book["video"]["path"]
    client = app.test_client()
    route = f"/api/v1/media/demo_book/{video}"
    assert client.get(route).status_code == 403
    assert client.get("/api/v1/books/demo_book").json["data"]["review_pending"]
    path = review_path(settings.data_dir,"demo_book")
    path.parent.mkdir()
    path.write_text(json.dumps({"fingerprint":fingerprint(store,book),
                                "human":{"reviewer":"test","decision":"approve"}}),encoding="utf-8")
    assert client.get(route).status_code == 200
    store.update("demo_book",lambda b: b["pages"][0].update(narration="changed"))
    assert client.get(route).status_code == 403


def test_index_cli_decodes_and_indexes_sample(monkeypatch,tmp_path):
    import json
    import sys
    from pathlib import Path
    from shutil import copytree
    from conftest import make_settings
    from agents.pipeline import build_pipeline
    from scripts import index_video

    pipe = build_pipeline(make_settings(tmp_path))
    copytree(Path("data/samples/demo_book"),pipe.store.book_dir("demo_book"))
    def analyze(uri,system,message,*,page_no,image_size):
        assert uri.startswith("data:image/jpeg;base64,") and image_size["width"] > 0
        return SimpleNamespace(text=json.dumps({"page_no":page_no,"scene_summary":"星星在树上",
                                                "objects":[{"name":"星星","bbox":[80,80,120,120]}]}))
    monkeypatch.setattr(index_video,"build_pipeline",lambda:pipe)
    monkeypatch.setattr(pipe.vlm,"status",lambda:{"available":True})
    monkeypatch.setattr(pipe.vlm,"_get_client",lambda:SimpleNamespace(analyze=analyze))
    monkeypatch.setenv("PBSC_EMBEDDINGS_MODEL","test")
    monkeypatch.setattr(index_video,"embed",lambda texts:[[0.2,0.8] for _ in texts])
    monkeypatch.setattr(sys,"argv",["index_video.py","demo_book","--step","1"])
    index_video.main()
    for page in pipe.store.load("demo_book")["pages"]:
        first = page["keyframes"][0]
        assert first["objects"] and first["embedding"] == [0.2,0.8]
        assert page["time_range"][0] <= first["time"] < page["time_range"][1]
