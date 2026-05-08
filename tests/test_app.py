import pytest  # type: ignore
import sys
import os
import json
from io import BytesIO

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app  # noqa: E402
import crop_pdf  # noqa: E402


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def test_index_route(client):
    """Test that index page loads."""
    response = client.get("/")
    assert response.status_code == 200


def test_upload_no_file(client):
    """Test upload endpoint with no file."""
    response = client.post("/upload")
    assert response.status_code == 400
    data = json.loads(response.data)
    assert "error" in data


def test_upload_invalid_file(client):
    """Test upload with non-PDF file."""
    data = {"file": (BytesIO(b"not a pdf"), "test.txt")}
    response = client.post("/upload", data=data)
    assert response.status_code == 400


def test_paginate_no_session(client):
    """Test paginate without uploaded PDF."""
    response = client.post(
        "/paginate",
        json={"cut_points": [100, 200, 300]},
        content_type="application/json",
    )
    assert response.status_code == 400
    data = json.loads(response.data)
    assert "error" in data


def test_detect_mode():
    """Test mode detection logic."""
    import fitz
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        doc = fitz.open()
        page = doc.new_page(width=595, height=842)
        page.insert_text((100, 200), "Test", fontsize=12)
        doc.save(f.name)
        doc.close()

        mode = crop_pdf.detect_mode(f.name)
        assert mode in ["text", "visual"]

        os.unlink(f.name)


def test_color_match():
    """Test color matching function."""
    assert crop_pdf.color_match((0.102, 0.549, 1.0), (0.102, 0.549, 1.0), tol=0.02)
    assert not crop_pdf.color_match((0.102, 0.549, 1.0), (1.0, 0.102, 0.251), tol=0.02)
    assert crop_pdf.color_match((0.11, 0.55, 0.99), (0.102, 0.549, 1.0), tol=0.02)


def test_upload_file_size_limit(client):
    """Test that large files are rejected."""
    large_data = b"x" * (50 * 1024 * 1024)  # 50MB
    data = {"file": (BytesIO(large_data), "large.pdf")}
    response = client.post("/upload", data=data)
    assert response.status_code in [400, 413, 500]


def test_paginate_invalid_json(client):
    """Test paginate with invalid JSON."""
    response = client.post("/paginate", data="not json", content_type="application/json")
    assert response.status_code == 400


def test_paginate_missing_cut_points(client):
    """Test paginate without cut_points."""
    response = client.post("/paginate", json={}, content_type="application/json")
    assert response.status_code in [400, 500]
