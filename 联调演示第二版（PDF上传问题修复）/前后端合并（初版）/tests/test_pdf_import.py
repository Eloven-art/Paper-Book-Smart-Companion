import io
import time

from conftest import make_settings
from server.app import create_app
from server.errors import OCRUnavailable


def pdf_with_pages(*texts):
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>",
               f"<< /Type /Pages /Count {len(texts)} /Kids [{' '.join(f'{4+2*i} 0 R' for i in range(len(texts)))}] >>".encode(),
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    for i, text in enumerate(texts):
        stream = f"BT /F1 14 Tf 14 120 Td ({text}) Tj ET".encode() if text else b""
        objects.extend([f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 180 160] /Resources << /Font << /F1 3 0 R >> >> /Contents {5+2*i} 0 R >>".encode(),
                        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"])
    buf = io.BytesIO(b"%PDF-1.4\n")
    buf.seek(0, 2)
    offsets = [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(buf.tell())
        buf.write(f"{i} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = buf.tell()
    buf.write(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        buf.write(f"{offset:010} 00000 n \n".encode())
    buf.write(f"trailer << /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return buf.getvalue()


def submit(client, data):
    result = client.post("/api/v1/pdf-imports",data={"pdf":(io.BytesIO(data),"test.pdf")})
    assert result.status_code == 202
    return result.json["data"]["id"]


def status(client, item):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        record = client.get(f"/api/v1/pdf-imports/{item}").json["data"]
        if record["status"] not in {"running", "cancelling"}:
            return record
        time.sleep(0.03)
    raise AssertionError("PDF import did not reach a stable state")


def test_pdf_pauses_then_skips_or_retries_only_failed_page(tmp_path,monkeypatch):
    app = create_app(make_settings(tmp_path))
    client = app.test_client()
    ext = app.extensions["pbsc"]
    tasks = ext["tasks"]
    attempted = []
    should_fail = True

    def ocr(image,*,book_id,page_no):
        attempted.append(page_no)
        if should_fail and page_no == 2:
            raise OCRUnavailable("测试 OCR 失败")
        return {"full_text": "A scanned story", "blocks":[]}

    monkeypatch.setattr(ext["pipeline"].ocr,"recognize_image",ocr)
    route = "/api/v1/pdf-imports"
    data = pdf_with_pages("The mouse found a house", "", "")
    try:
        for action in ("skip","retry"):
            should_fail = True
            attempted.clear()
            item = submit(client,data)
            paused = status(client,item)
            assert paused["status"] == "paused" and paused["page"] == 2
            assert attempted == [2]
            assert client.get(f"/api/v1/books/{item}").status_code == 404
            should_fail = False
            assert client.post(f"{route}/{item}/decision",json={"action":action,"page":2}).status_code == 200
            assert status(client,item)["status"] == "completed"
            assert attempted == ([2,3] if action == "skip" else [2,2,3])
            pages = client.get(f"/api/v1/books/{item}/pages").json["data"]["pages"]
            assert [page["page_no"] for page in pages] == [1,2,3]
            assert pages[0]["source_reference"]["text_source"] == "pdf_text_layer"
            assert pages[0]["ocr_text"]["blocks"][0]["bbox"][2] > 0
            assert bool(pages[1].get("import_skipped")) == (action == "skip")
    finally:
        tasks.shutdown(wait=True)


def test_invalid_pdf_is_retained_then_deleted(tmp_path):
    app = create_app(make_settings(tmp_path))
    client = app.test_client()
    tasks = app.extensions["pbsc"]["tasks"]
    try:
        data = b"%PDF-1.4 corrupted"
        item = submit(client,data)
        assert status(client,item)["status"] == "trash"
        route = f"/api/v1/pdf-trash/{item}"
        with client.get(route+"/original") as response:
            assert response.status_code == 200 and response.data == data
        assert client.delete(route).status_code == 200
        assert client.get(route+"/original").status_code == 404
    finally:
        tasks.shutdown(wait=True)
