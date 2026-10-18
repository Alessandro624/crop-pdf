import os
import sys
from io import BytesIO
import pytest  # type: ignore

try:
    import pymupdf as fitz  # type: ignore
except ImportError:
    import fitz  # type: ignore

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import app  # noqa: E402


def _make_pdf_bytes(width=595, height=2400):
    doc = fitz.open()
    page = doc.new_page(width=width, height=height)
    page.insert_text((80, 200), "Section 1", fontsize=18)
    page.insert_text((80, 950), "Section 2", fontsize=18)
    page.insert_text((80, 1800), "Section 3", fontsize=18)
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def test_index_route(client):
    response = client.get("/")
    assert response.status_code == 200


def test_upload_no_file(client):
    response = client.post("/upload")
    assert response.status_code == 400
    assert "error" in response.get_json()


def test_upload_invalid_extension(client):
    data = {"file": (BytesIO(b"not a pdf"), "test.txt")}
    response = client.post("/upload", data=data)
    assert response.status_code == 400


def test_multi_tab_upload_and_paginate(client):
    """Test that two concurrent tabs get distinct job_ids and do not overwrite each other."""
    pdf1 = _make_pdf_bytes(height=2000)
    pdf2 = _make_pdf_bytes(height=3000)

    r1 = client.post(
        "/upload",
        data={"file": (BytesIO(pdf1), "doc1.pdf"), "page_format": "a4", "cut_mode": "smart"},
    )
    r2 = client.post(
        "/upload",
        data={"file": (BytesIO(pdf2), "doc2.pdf"), "page_format": "tablet_4_3", "cut_mode": "exact"},
    )

    assert r1.status_code == 200
    assert r2.status_code == 200
    d1 = r1.get_json()
    d2 = r2.get_json()

    assert d1["job_id"] != d2["job_id"]
    assert d1["total_height"] == 2000
    assert d2["total_height"] == 3000

    # Recalculate on tab 1
    rec = client.post(
        "/recalculate",
        json={"job_id": d1["job_id"], "page_format": "square", "cut_mode": "exact"},
    )
    assert rec.status_code == 200
    assert len(rec.get_json()["cut_points"]) >= 2

    # Paginate tab 1 with human feedback shift
    shifted_cuts = [c - 25.0 for c in d1["cut_points"]] if d1["cut_points"] else [800.0]
    pag = client.post(
        "/paginate",
        json={"job_id": d1["job_id"], "cut_points": shifted_cuts, "name": "out1"},
    )
    assert pag.status_code == 200
    assert pag.mimetype == "application/pdf"


def test_security_invalid_job_id_rejected(client):
    """Ensure path traversal or malformed job_ids are rejected."""
    bad_ids = ["../../../etc/passwd", "not-a-valid-hex", "", None, 12345]
    for bad in bad_ids:
        res = client.post("/paginate", json={"job_id": bad, "cut_points": [500]})
        assert res.status_code == 400


def test_paginate_non_numeric_cut_points(client):
    pdf = _make_pdf_bytes(height=2000)
    up = client.post("/upload", data={"file": (BytesIO(pdf), "doc.pdf")}).get_json()
    res = client.post(
        "/paginate",
        json={"job_id": up["job_id"], "cut_points": ["invalid", "number"]},
    )
    assert res.status_code == 400
