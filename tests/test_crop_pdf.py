import pytest  # noqa: F401
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import crop_pdf  # noqa: E402


def test_clean_dark_bars_removes_dark_rectangles(tmp_path):
    """Test that dark bars are removed from PDF."""
    import fitz

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    # Add a dark rectangle
    page.draw_rect(fitz.Rect(50, 100, 545, 120), color=None, fill=(0.3, 0.3, 0.3))
    # Add some text
    page.insert_text((100, 200), "Test Document", fontsize=20)

    input_path = str(tmp_path / "input.pdf")
    output_path = str(tmp_path / "cleaned.pdf")
    doc.save(input_path)
    doc.close()

    removed = crop_pdf.clean_dark_bars(input_path, output_path)
    assert removed >= 1
    assert os.path.exists(output_path)


def test_clean_dark_bars_no_bars(tmp_path):
    """Test that no bars are removed when none exist."""
    import fitz

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((100, 200), "Test Document", fontsize=20)

    input_path = str(tmp_path / "input.pdf")
    output_path = str(tmp_path / "cleaned.pdf")
    doc.save(input_path)
    doc.close()

    removed = crop_pdf.clean_dark_bars(input_path, output_path)
    assert removed == 0


def test_find_cut_points_batch(tmp_path):
    """Test cut point detection."""
    import fitz

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    # Add title-like content at specific positions
    page.insert_text((100, 150), "TITLE ONE", fontsize=16)
    page.insert_text((100, 400), "TITLE TWO", fontsize=16)

    input_path = str(tmp_path / "test.pdf")
    doc.save(input_path)
    doc.close()

    title_tops = [140.0, 390.0]
    cut_points = crop_pdf.find_cut_points_batch(input_path, title_tops, search_range=80.0)
    assert len(cut_points) == 2
    assert all(0 < cp < 842 for cp in cut_points)


def test_compute_pages_single_page():
    """Test page computation for single page."""
    title_tops = [100.0, 300.0, 500.0]
    total_height = 842.0
    pages = crop_pdf.compute_pages(title_tops, total_height, padding=14.0, max_page_ratio=1.15, min_last_page=120.0, a4_height=842.0)
    assert len(pages) >= 1
    assert all(start < end for start, end in pages)


def test_compute_pages_small_last_page():
    """Test that small last pages get merged."""
    title_tops = [100.0, 300.0, 700.0]
    total_height = 842.0
    pages = crop_pdf.compute_pages(title_tops, total_height, padding=14.0, max_page_ratio=1.15, min_last_page=200.0, a4_height=842.0)
    assert len(pages) >= 1


def test_compute_pages_edge_case_empty():
    """Test compute_pages with no titles."""
    pages = crop_pdf.compute_pages([], 842.0, padding=14.0, max_page_ratio=1.15, min_last_page=120.0, a4_height=842.0)
    assert len(pages) == 0


def test_compute_pages_single_title():
    """Test compute_pages with single title."""
    pages = crop_pdf.compute_pages([400.0], 842.0, padding=14.0, max_page_ratio=1.15, min_last_page=120.0, a4_height=842.0)
    assert len(pages) >= 1


def test_color_match():
    """Test color matching function."""
    assert crop_pdf.color_match((0.102, 0.549, 1.0), (0.102, 0.549, 1.0), tol=0.02)
    assert not crop_pdf.color_match((0.102, 0.549, 1.0), (1.0, 0.102, 0.251), tol=0.02)
    assert crop_pdf.color_match((0.11, 0.55, 0.99), (0.102, 0.549, 1.0), tol=0.02)
