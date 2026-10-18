import os
import sys

try:
    import pymupdf as fitz  # type: ignore
except ImportError:
    import fitz  # type: ignore

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import crop_pdf  # noqa: E402


def test_clean_dark_bars_removes_dark_rectangles(tmp_path):
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.draw_rect(fitz.Rect(50, 100, 545, 120), color=None, fill=(0.2, 0.2, 0.2))
    page.insert_text((100, 200), "Test Document", fontsize=20)

    input_path = str(tmp_path / "input.pdf")
    output_path = str(tmp_path / "cleaned.pdf")
    doc.save(input_path)
    doc.close()

    removed = crop_pdf.clean_dark_bars(input_path, output_path)
    assert removed >= 1
    assert os.path.exists(output_path)


def test_find_format_cuts_exact_and_smart(tmp_path):
    doc = fitz.open()
    page = doc.new_page(width=595, height=2600)
    for y in range(100, 2500, 150):
        page.insert_text((80, y), f"Line at {y}", fontsize=14)

    pdf_path = str(tmp_path / "long.pdf")
    doc.save(pdf_path)
    doc.close()

    cuts_exact, w, h = crop_pdf.find_format_cuts(pdf_path, page_format="a4", smart=False)
    cuts_smart, _, _ = crop_pdf.find_format_cuts(pdf_path, page_format="a4", smart=True)

    assert w == 595
    assert h == 2600
    assert len(cuts_exact) >= 2
    assert len(cuts_smart) >= 2
    assert all(0 < c < 2600 for c in cuts_smart)


def test_feedback_weight_learning(tmp_path, monkeypatch):
    weights_file = str(tmp_path / "weights.json")
    monkeypatch.setattr(crop_pdf, "WEIGHTS_FILE", weights_file)

    doc = fitz.open()
    page = doc.new_page(width=595, height=2000)
    page.insert_text((80, 840), "Text right on A4 boundary", fontsize=16)
    pdf_path = str(tmp_path / "feedback.pdf")
    doc.save(pdf_path)
    doc.close()

    initial_w = crop_pdf.load_weights()
    updated = crop_pdf.update_weights_from_feedback(
        pdf_path,
        auto_cuts=[841.0],
        human_cuts=[790.0],
        page_format="a4",
    )
    assert updated is True
    new_w = crop_pdf.load_weights()
    assert new_w != initial_w
