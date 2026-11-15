import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from agents.cost import CostLedger
from server.errors import BudgetExceeded


def test_parallel_reservations_and_unknown_price(tmp_path):
    price = tmp_path / "pricing.json"
    price.write_text(json.dumps({"services":{"i2v":{"demo":{"per_second":1}}},
                                 "budgets":{"total":{"max_amount_total":5}}}),encoding="utf-8")
    ledger = CostLedger(tmp_path/"usage.jsonl",price)

    def remaining(model):
        with ledger.reserve("i2v",model,{"seconds":3}):
            raise AssertionError("unsafe reservation was allowed")

    with ledger.reserve("i2v","demo",{"seconds":3}):
        with ThreadPoolExecutor() as pool:
            for model in ("demo","unknown"):
                with pytest.raises(BudgetExceeded):
                    pool.submit(remaining,model).result()
        ledger.record("i2v","demo",{"seconds":3})
    with ledger.reserve("i2v","demo",{"seconds":2}):
        ledger.record("i2v","demo",{"seconds":2})
    assert ledger.summary()["i2v"]["amount"] == 5


def test_pdf_background_generation_reports_failed_pages_and_can_resume(tmp_path, monkeypatch):
    from conftest import make_settings
    from test_pdf_import import pdf_with_pages, submit, status
    from server.app import create_app
    app = create_app(make_settings(tmp_path, PBSC_AUTO_GENERATE="1"))
    client = app.test_client()
    ext = app.extensions["pbsc"]
    pipe, tasks = ext["pipeline"], ext["tasks"]
    video_calls = []

    def video(book_id, *, pages, progress, check_cancelled):
        check_cancelled()
        video_calls.append(pages)
        return {"duration": 1}

    monkeypatch.setattr(pipe.vlm,"status",lambda:{"available":False})
    monkeypatch.setattr(pipe.narration,"generate_book",lambda *a,**kw: {"pages":[]})
    monkeypatch.setattr(pipe.video,"generate_book_video",video)
    try:
        item = submit(client,pdf_with_pages("The mouse found a house"))
        assert status(client,item)["status"] == "completed"
        imports = tasks.list(book_id=item,kind="pdf_import")
        tasks.wait(imports[0]["task_id"])
        first = tasks.list(book_id=item,kind="auto_generate")[0]
        result = tasks.wait(first["task_id"])
        assert result["status"] == "succeeded"
        assert result["result"]["vlm_errors"] == [{"page_no":1,"code":"VLM_UNAVAILABLE"}]
        second = client.post(f"/api/v1/books/{item}/auto-generate").json["data"]
        assert second["task_id"] != first["task_id"]
        assert tasks.wait(second["task_id"])["status"]=="succeeded"
        assert tasks.list(book_id=item,kind="auto_generate")[0]["task_id"]==second["task_id"]
        assert video_calls == [[1],[1]]
    finally:
        tasks.shutdown(wait=True)
