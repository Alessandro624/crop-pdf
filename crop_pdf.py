#!/usr/bin/env python3
"""
crop_pdf.py - Fast format-based PDF pagination with smart whitespace snapping and preference learning.
Dependencies: pip install pymupdf numpy
"""

import argparse
import json
import os
import tempfile
import threading
import numpy as np  # type: ignore

try:
    import pymupdf as fitz  # type: ignore
except ImportError:
    import fitz  # type: ignore

fitz.TOOLS.mupdf_display_errors(False)
fitz.TOOLS.mupdf_display_warnings(False)

# Supported formats: Height / Width ratio
PAGE_FORMATS = {
    "a4": 1.4142,  # ISO A4 / A3 / A5 portrait (sqrt(2))
    "a4_land": 0.7071,  # ISO landscape (1 / sqrt(2))
    "tablet_4_3": 1.3333,  # 4:3 portrait tablet (iPad)
    "16_9": 1.7778,  # 16:9 portrait screen
    "square": 1.0,  # 1:1 square
}

# Default weights for Smart Cut:
# [w_ink_line, w_ink_near, w_dist_target, w_gap_bonus]
DEFAULT_WEIGHTS = [50.0, 12.0, 2.5, 1.8]
WEIGHTS_FILE = os.environ.get("CROP_PDF_WEIGHTS", "/tmp/crop_pdf_weights.json")
WEIGHTS_LOCK = threading.Lock()

# Dark bar cleanup parameters
CLEAN_DARK_FILL = 0.5
CLEAN_MIN_WIDTH = 0.20
CLEAN_MIN_HEIGHT = 2.0
CLEAN_EXPAND = 2.0


def load_weights():
    """Loads learned weights from the JSON file (or returns the defaults)."""
    with WEIGHTS_LOCK:
        if os.path.exists(WEIGHTS_FILE):
            try:
                with open(WEIGHTS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                w = data.get("weights", DEFAULT_WEIGHTS)
                if isinstance(w, list) and len(w) == 4:
                    return [float(x) for x in w]
            except Exception:
                pass
        return list(DEFAULT_WEIGHTS)


def save_weights(weights, updates_count=1):
    """Saves the updated weights."""
    with WEIGHTS_LOCK:
        try:
            with open(WEIGHTS_FILE, "w", encoding="utf-8") as f:
                json.dump(
                    {"weights": [round(float(x), 4) for x in weights], "updates": updates_count},
                    f,
                )
        except OSError:
            pass


def clean_dark_bars(
    input_path,
    output_path,
    dark_fill=CLEAN_DARK_FILL,
    min_width=CLEAN_MIN_WIDTH,
    min_height=CLEAN_MIN_HEIGHT,
    expand=CLEAN_EXPAND,
):
    """Overlays white rectangles on dark selection bars left in the PDF."""
    doc = fitz.open(input_path)
    page = doc[0]
    W = page.rect.width
    removed = 0
    for d in page.get_drawings():
        fill = d.get("fill")
        if not (isinstance(fill, tuple) and len(fill) == 3):
            continue
        r, g, b = fill
        if not (r < dark_fill and g < dark_fill and b < dark_fill):
            continue
        rect = d.get("rect")
        if rect is None:
            continue
        rw = rect[2] - rect[0]
        rh = rect[3] - rect[1]
        if rw > W * min_width and rh >= min_height:
            expanded = fitz.Rect(rect) + (-expand, -expand, expand, expand)
            page.draw_rect(expanded, color=None, fill=(1, 1, 1), overlay=True)
            removed += 1
    doc.save(
        output_path,
        garbage=4,
        deflate=True,
        clean=True,
        deflate_images=True,
        deflate_fonts=True,
    )
    doc.close()
    return removed


def compute_ink_profile(pdf_path, dpi=72):
    """
    Renders the page in grayscale at 72 DPI (1 px = 1 pt) and computes:
    - ink_row: fraction of dark pixels in each row (0.0 = white, 1.0 = black)
    - ink_near: moving average of ink density in the surrounding vertical neighborhood
    - gap_height: normalized height of the continuous whitespace around each row
    """
    doc = fitz.open(pdf_path)
    page = doc[0]
    W, H = page.rect.width, page.rect.height
    zoom = dpi / 72.0
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), colorspace=fitz.csGRAY, alpha=False)
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
    doc.close()

    margin_px = max(1, int(pix.width * 0.02))
    core = arr[:, margin_px:-margin_px] if pix.width > 2 * margin_px else arr

    ink_row = np.mean(core < 242, axis=1).astype(np.float32)

    kernel_size = max(3, int(12 * zoom))
    kernel = np.ones(kernel_size, dtype=np.float32) / kernel_size
    ink_near = np.convolve(ink_row, kernel, mode="same")

    is_white = ink_row < 0.002
    gap_height = np.zeros_like(ink_row)
    n = len(is_white)
    i = 0
    while i < n:
        if is_white[i]:
            j = i
            while j < n and is_white[j]:
                j += 1
            length_pt = (j - i) / zoom
            gap_height[i:j] = min(length_pt, 60.0) / 40.0
            i = j
        else:
            i += 1

    return ink_row, ink_near, gap_height, W, H, zoom


def extract_row_features(px, target_px, target_h_px, ink_row, ink_near, gap_height):
    """Extracts the 4-feature vector for a candidate row px."""
    f_ink = float(ink_row[px])
    f_near = float(ink_near[px])
    f_dist = abs(px - target_px) / max(1.0, float(target_h_px))
    f_gap = -float(gap_height[px])
    return np.array([f_ink, f_near, f_dist, f_gap], dtype=np.float32)


def find_format_cuts(
    pdf_path,
    page_format="a4",
    smart=True,
    tol_up=0.18,
    tol_down=0.06,
    min_last_ratio=0.20,
):
    """
    Computes cut points based on the selected page format.
    - If smart=False: cuts exactly every H_target points.
    - If smart=True: uses the ink profile and learned weights to cut at the best whitespace location.
    """
    ratio = PAGE_FORMATS.get(page_format, 1.4142)
    ink_row, ink_near, gap_height, W, H, zoom = compute_ink_profile(pdf_path, dpi=72)
    target_h = W * ratio

    if H <= target_h * (1.0 + tol_down):
        return [], W, H

    if not smart:
        cuts = []
        y = target_h
        while y < H - (target_h * min_last_ratio):
            cuts.append(round(float(y), 1))
            y += target_h
        return cuts, W, H

    weights = np.array(load_weights(), dtype=np.float32)
    n_px = len(ink_row)
    target_h_px = target_h * zoom

    cuts = []
    cur_y_pt = 0.0

    while True:
        ideal_y_pt = cur_y_pt + target_h
        if ideal_y_pt >= H - (target_h * min_last_ratio):
            break

        min_y_pt = max(cur_y_pt + target_h * 0.35, ideal_y_pt - target_h * tol_up)
        max_y_pt = min(H - 20.0, ideal_y_pt + target_h * tol_down)

        px_start = max(0, int(min_y_pt * zoom))
        px_end = min(n_px - 1, int(max_y_pt * zoom))
        target_px = int(ideal_y_pt * zoom)

        if px_end <= px_start:
            best_y_pt = ideal_y_pt
        else:
            idx = np.arange(px_start, px_end + 1)
            f_ink = ink_row[idx]
            f_near = ink_near[idx]
            f_dist = np.abs(idx - target_px) / max(1.0, target_h_px)
            f_gap = -gap_height[idx]

            scores = weights[0] * f_ink + weights[1] * f_near + weights[2] * f_dist + weights[3] * f_gap
            best_idx = int(idx[int(np.argmin(scores))])
            best_y_pt = best_idx / zoom

        cuts.append(round(float(best_y_pt), 1))
        cur_y_pt = best_y_pt

    return cuts, W, H


def update_weights_from_feedback(pdf_path, auto_cuts, human_cuts, page_format="a4", lr=0.35):
    """
    Online learning (Pairwise Ranking):
    Compares the proposed cuts (auto_cuts) with those selected by the user (human_cuts).
    """
    if not auto_cuts or not human_cuts:
        return False

    ink_row, ink_near, gap_height, W, H, zoom = compute_ink_profile(pdf_path, dpi=72)
    ratio = PAGE_FORMATS.get(page_format, 1.4142)
    target_h_px = W * ratio * zoom
    n_px = len(ink_row)

    weights = np.array(load_weights(), dtype=np.float32)
    updated = False

    for y_auto in auto_cuts:
        closest_human = min(human_cuts, key=lambda yh: abs(yh - y_auto))
        diff_pt = abs(closest_human - y_auto)

        if 4.0 <= diff_pt <= 220.0:
            px_auto = min(n_px - 1, max(0, int(y_auto * zoom)))
            px_hum = min(n_px - 1, max(0, int(closest_human * zoom)))

            f_auto = extract_row_features(px_auto, px_auto, target_h_px, ink_row, ink_near, gap_height)
            f_hum = extract_row_features(px_hum, px_auto, target_h_px, ink_row, ink_near, gap_height)

            grad = f_hum - f_auto
            weights = weights - lr * grad
            weights = np.clip(weights, [5.0, 1.0, 0.2, 0.1], [200.0, 60.0, 15.0, 15.0])
            updated = True

    if updated:
        save_weights(weights.tolist())
    return updated


def write_pdf(input_path, output_path, pages, page_width, verbose=False):
    """Writes the final PDF with maximum deduplication (garbage=4) and full compression."""
    src = fitz.open(input_path)
    out = fitz.open()
    for i, (y0, y1) in enumerate(pages):
        h = y1 - y0
        pg = out.new_page(width=page_width, height=h)
        pg.show_pdf_page(
            fitz.Rect(0, 0, page_width, h),
            src,
            0,
            clip=fitz.Rect(0, y0, page_width, y1),
        )
        if verbose:
            print(f"  Page {i + 1:>2}: {h:.0f}pt ({h / 72 * 25.4:.0f}mm)")

    out.save(
        output_path,
        garbage=4,
        deflate=True,
        clean=True,
        deflate_images=True,
        deflate_fonts=True,
    )
    out.close()
    src.close()


def paginate_pdf(input_path, output_path, page_format="a4", smart=True, clean=True, verbose=True):
    """Main CLI entry point."""
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"File not found: {input_path}")

    working_path = input_path
    tmp_clean = None
    if clean:
        fd, tmp_clean = tempfile.mkstemp(suffix="_clean.pdf")
        os.close(fd)
        n_removed = clean_dark_bars(input_path, tmp_clean)
        if verbose and n_removed > 0:
            print(f"[INFO] Clean: {n_removed} dark bars removed")
        working_path = tmp_clean

    try:
        cuts, W, H = find_format_cuts(working_path, page_format=page_format, smart=smart)
        boundaries = [0.0] + cuts + [H]
        pages = [(boundaries[i], boundaries[i + 1]) for i in range(len(boundaries) - 1) if boundaries[i + 1] - boundaries[i] > 10.0]
        if verbose:
            print(f"[INFO] Format: {page_format.upper()} ({'smart' if smart else 'exact'}) -> {len(pages)} pages")
        write_pdf(working_path, output_path, pages, W, verbose=verbose)
        return len(pages)
    finally:
        if tmp_clean and os.path.exists(tmp_clean):
            os.remove(tmp_clean)


def main():
    p = argparse.ArgumentParser(description="Smart format-based PDF paginator.")
    p.add_argument("input", help="Source PDF (vertical scroll)")
    p.add_argument("-o", "--output", help="Output PDF")
    p.add_argument("-f", "--format", choices=list(PAGE_FORMATS.keys()), default="a4", help="Target page format")
    p.add_argument("--exact", action="store_true", help="Cut at exact format height (disable smart whitespace snap)")
    p.add_argument("--no-clean", action="store_true", help="Disable dark bar removal")
    p.add_argument("-q", "--quiet", action="store_true", help="Quiet mode")
    args = p.parse_args()

    out_path = args.output or (os.path.splitext(args.input)[0] + "_paginated.pdf")
    paginate_pdf(
        input_path=args.input,
        output_path=out_path,
        page_format=args.format,
        smart=not args.exact,
        clean=not args.no_clean,
        verbose=not args.quiet,
    )


if __name__ == "__main__":
    main()
