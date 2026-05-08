#!/usr/bin/env python3
"""
crop_pdf.py - Automatic PDF pagination

AUTOMATIC PAGINATION:
    - "text"  : the PDF has selectable text with colors in metadata (pdfplumber)
    - "visual": the PDF uses encoded/custom fonts -> pixel HSV analysis (PyMuPDF + NumPy)
    The mode is selected automatically, or forced with --mode text/visual.

USAGE:
        python crop_pdf.py input.pdf
        python crop_pdf.py input.pdf -o output.pdf
        python crop_pdf.py input.pdf --debug-colors        # color diagnostic mode
        python crop_pdf.py input.pdf --mode visual --zoom 2.0

DEPENDENCIES:
        pip install pdfplumber pymupdf numpy
"""

import argparse
import os
import sys
import warnings

warnings.filterwarnings("ignore", category=RuntimeWarning)
import fitz as _fitz_init  # noqa: E402

_fitz_init.TOOLS.mupdf_display_errors(False)
_fitz_init.TOOLS.mupdf_display_warnings(False)

try:
    import pdfplumber  # noqa: E402
    import fitz  # noqa: E402
    import numpy as np  # noqa: E402
except ImportError as e:
    print(f"Dependency missing: {e}")
    print("Install with: pip install pdfplumber pymupdf numpy")
    sys.exit(1)


# DEFAULT CONFIGURATION

# Cleaning
CLEAN_DARK_BARS = True  # remove rectangles that cover the text
CLEAN_DARK_FILL = 0.5  # threshold: fill < N on all channels = "dark"
CLEAN_MIN_WIDTH = 0.20  # minimum width of bar (fraction of the page)
CLEAN_MIN_HEIGHT = 2.0  # minimum height of bar (pt)
CLEAN_EXPAND = 2.0  # expansion of the white rectangle covering (pt)

# Pagination common
A4_HEIGHT_PT = 842.0  # height of A4 in points (297mm)
TITLE_PADDING = 14.0  # pt of space before title in the cut
MAX_PAGE_RATIO = 1.15  # max page height = ratio * A4
MIN_LAST_PAGE = 120.0  # threshold for merging last page (pt)

# TEXT MODE (pdfplumber)
MIN_TITLE_SIZE = 13.5  # minimum font size for titles
TITLE_MERGE_GAP = 60.0  # gap (pt) under which close titles are merged into one (multirow)
MAX_TITLE_X0 = 50.0  # max left margin for considering aligned text

# Title colors RGB 0..1 - comparison with tolerance +/- COLOR_TOL
TITLE_COLORS = [
    (0.102, 0.549, 1.0),  # blue
    (1.0, 0.102, 0.251),  # red
]
COLOR_TOL = 0.02

# VISUAL MODE (PyMuPDF + NumPy)
VISUAL_ZOOM = 1.5  # factor of rasterization (pt -> pixel)
VISUAL_STRIPE = 1500  # height of rasterization strip (pt)
VISUAL_MIN_BAND_H = 14.0  # minimum height of colored band (pt) to be considered a title
VISUAL_MAX_BAND_H = 50.0  # maximum height (beyond = image/figure, not a title)
VISUAL_MIN_RATIO = 0.08  # minimum fraction of width with colored pixels
VISUAL_MAX_LEFT = 0.12  # maximum fraction of width for the leftmost pixel

# HUE RANGE (0-360 degrees) for blue and red
HUE_BLUE = (195, 225)
HUE_RED_A = (330, 360)  # red "high"
HUE_RED_B = (0, 15)  # red "low" (wrap)
MIN_SAT = 0.35  # minimum saturation
MIN_VAL = 0.20  # minimum value (brightness)


# COMMON UTILS


def clean_dark_bars(input_path, output_path, dark_fill=CLEAN_DARK_FILL, min_width=CLEAN_MIN_WIDTH, min_height=CLEAN_MIN_HEIGHT, expand=CLEAN_EXPAND):
    """
    Overwrite with white the dark rectangles that cover the text
    (selection/highlighting bars left in the annotations).
    Saves the clean PDF in output_path and returns the number of bars removed.
    """
    doc = _open_pdf_silent(input_path)
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
    doc.save(output_path, garbage=4, deflate=True)
    doc.close()
    return removed


def find_cut_points_batch(pdf_path, title_tops, search_range=80.0, zoom=1.5):
    """
    For each title, find the optimal cut point by searching for the whitest line
    (gap between text) in the range [title - search_range, title].
    Opens the PDF only once for efficiency.
    """
    doc = _open_pdf_silent(pdf_path)
    page = doc[0]
    W = page.rect.width
    H = page.rect.height
    cut_points = []

    for title_top in title_tops:
        y0 = max(0.0, title_top - search_range)
        y1 = min(title_top + 2.0, H)  # +2pt to include the title line as a boundary

        if y1 - y0 < 4:
            cut_points.append(max(0.0, title_top - 5))
            continue

        clip = fitz.Rect(0, y0, W, y1)
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip, colorspace=fitz.csRGB)
        arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)

        # Count dark pixels per row (text = dark, space = white)
        dark_counts = np.sum(arr.min(axis=2) < 230, axis=1)

        # Search for the completely white row closest to the title (scan from top to bottom
        # in the last part of the range, to find the space AFTER the last row of text
        # and BEFORE the title, which is the best cut point)
        best_px = 0
        best_dark = dark_counts[0] if len(dark_counts) > 0 else 0

        for px in range(len(dark_counts) - 1, -1, -1):
            if dark_counts[px] < best_dark:
                best_dark = dark_counts[px]
                best_px = px
            if best_dark == 0:
                break

        cut_pt = y0 + best_px / zoom
        cut_points.append(cut_pt)

    doc.close()
    return cut_points


def compute_pages(title_tops, total_height, padding, max_page_ratio, min_last_page, a4_height, working_path=None, zoom=1.5):
    """
    DP: group the sections to minimize the deviation from A4.
    The cut point is calculated by searching for the whitest line
    (gap between text) in the range [title - search_range, title].
    If working_path is None, uses fixed padding as a fallback.
    Single sections > limit are accepted by themselves.
    The last page too small is merged with the previous one.
    """
    if not title_tops:
        return []
    max_page_h = a4_height * max_page_ratio
    if working_path:
        cut_points = find_cut_points_batch(working_path, title_tops, search_range=padding * 4, zoom=zoom)
    else:
        cut_points = [max(0.0, t - padding) for t in title_tops]
    boundaries = cut_points + [total_height]
    sections = [(boundaries[i], boundaries[i + 1], boundaries[i + 1] - boundaries[i]) for i in range(len(cut_points))]
    n = len(sections)
    max_single = max(s[2] for s in sections)

    def cost(h):
        if h > max(max_page_h, max_single):
            return float("inf")
        return (h - a4_height) ** 2

    INF = float("inf")
    dp = [INF] * (n + 1)
    dp[0] = 0.0
    prev = [-1] * (n + 1)

    for i in range(1, n + 1):
        cumh = 0.0
        for j in range(i - 1, -1, -1):
            cumh += sections[j][2]
            if cumh > max_page_h and j != i - 1:
                break
            c = dp[j] + cost(cumh)
            if c < dp[i]:
                dp[i] = c
                prev[i] = j

    cuts_idx = []
    idx = n
    while idx > 0:
        cuts_idx.append(idx)
        idx = prev[idx]
    cuts_idx.reverse()

    pages = []
    start = 0
    for end in cuts_idx:
        pages.append((sections[start][0], sections[end - 1][1]))
        start = end

    if len(pages) > 1 and (pages[-1][1] - pages[-1][0]) < min_last_page:
        last = pages.pop()
        p0, _ = pages.pop()
        pages.append((p0, last[1]))

    return pages


def _open_pdf_silent(path):
    """Opens a PDF with fitz, suppressing MuPDF warnings (written to C-level stderr)."""
    import os

    # Redirect fd 1 (C stdout) to /dev/null during opening (MuPDF writes to stdout)
    devnull = os.open(os.devnull, os.O_WRONLY)
    old_fd = os.dup(1)
    os.dup2(devnull, 1)
    os.close(devnull)
    try:
        doc = fitz.open(path)
    finally:
        os.dup2(old_fd, 1)
        os.close(old_fd)
    return doc


def write_pdf(input_path, output_path, pages, page_width, verbose):
    src = _open_pdf_silent(input_path)
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
            ratio = h / A4_HEIGHT_PT
            flag = " [LARGE]" if h > A4_HEIGHT_PT * MAX_PAGE_RATIO else ""
            print(f"  Page {i + 1:>2}: {h:.0f}pt = {h / 72 * 25.4:.0f}mm ({ratio:.2f}xA4){flag}")
    out.save(output_path, garbage=4, deflate=True)
    out.close()
    src.close()


# TEXT MODE  (pdfplumber)


def color_match(a, b, tol=COLOR_TOL):
    if not isinstance(a, tuple) or not isinstance(b, tuple) or len(a) != len(b):
        return False
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def is_title_color_text(char, title_colors):
    col = char.get("non_stroking_color")
    if not isinstance(col, tuple):
        return False
    return any(color_match(col, tc) for tc in title_colors)


def find_titles_text(pdf_path, min_size, merge_gap, max_x0, title_colors, timeout_sec=25):
    """
    Extract titles via color metadata (pdfplumber), in subprocess with timeout.
    Returns (title_tops, total_height, page_width) or None if timeout/fails.
    """
    import subprocess
    import sys
    import json

    script = """
import sys, json
import pdfplumber

pdf_path   = sys.argv[1]
min_size   = float(sys.argv[2])
merge_gap  = float(sys.argv[3])
max_x0     = float(sys.argv[4])
tol        = float(sys.argv[5])
tc_raw     = sys.argv[6]  # "r,g,b;r,g,b"

def parse_colors(s):
    result = []
    for e in s.split(';'):
        parts = [float(x) for x in e.split(',')]
        result.append(tuple(parts))
    return result

def color_match(a, b, tol):
    if not isinstance(a, tuple) or not isinstance(b, tuple) or len(a)!=len(b): return False
    return all(abs(x-y)<=tol for x,y in zip(a,b))

title_colors = parse_colors(tc_raw)

try:
    with pdfplumber.open(pdf_path) as pdf:
        page  = pdf.pages[0]
        chars = page.chars

        colored = [c for c in chars
                   if isinstance(c.get('non_stroking_color'), tuple)
                   and any(color_match(c['non_stroking_color'], tc, tol) for tc in title_colors)]

        if not colored:
            print(json.dumps({'tops': [], 'height': page.height, 'width': page.width, 'no_color': True}))
            sys.exit(0)

        colored_sorted = sorted(colored, key=lambda c: c['top'])
        lines = [[colored_sorted[0]]]
        for c in colored_sorted[1:]:
            if abs(c['top'] - lines[-1][-1]['top']) < 5:
                lines[-1].append(c)
            else:
                lines.append([c])

        raw_tops = []
        for line in lines:
            text  = ''.join(c['text'] for c in sorted(line, key=lambda c: c['x0']))
            size  = max(c['size'] for c in line)
            top   = min(c['top']  for c in line)
            x0    = min(c['x0']   for c in line)
            clean = text.replace(' ', '').replace('->', '').replace('\\u2022', '')
            if clean.isupper() and len(clean)>1 and size>=min_size and x0<=max_x0:
                raw_tops.append(top)

        merged = []
        if raw_tops:
            merged = [raw_tops[0]]
            for t in raw_tops[1:]:
                if t - merged[-1] >= merge_gap:
                    merged.append(t)

        print(json.dumps({'tops': merged, 'height': page.height, 'width': page.width}))
except Exception as e:
    print(json.dumps({'tops': [], 'height': 0, 'width': 0, 'error': str(e)}))
"""

    tc_str = ";".join(",".join(str(x) for x in tc) for tc in title_colors)
    try:
        result = subprocess.run([sys.executable, "-c", script, pdf_path, str(min_size), str(merge_gap), str(max_x0), str(COLOR_TOL), tc_str], capture_output=True, text=True, timeout=timeout_sec)
        if not result.stdout.strip():
            return None, None, None
        data = json.loads(result.stdout.strip())
        if data.get("no_color"):
            return None, data["height"], data["width"]
        return data["tops"], data["height"], data["width"]
    except subprocess.TimeoutExpired:
        return None, None, None
    except Exception:
        return None, None, None


def debug_colors_text(pdf_path, min_size, max_x0, title_colors, timeout_sec=15):
    import signal

    def _handler(sig, frame):
        raise TimeoutError()

    signal.signal(signal.SIGALRM, _handler)
    signal.alarm(timeout_sec)
    try:
        with pdfplumber.open(pdf_path) as pdf:
            page = pdf.pages[0]
            chars = page.chars

        colors = {}
        for c in chars:
            col = c.get("non_stroking_color")
            if col is not None and col != (0, 0, 0) and col != 0:
                key = str(col)
                colors[key] = colors.get(key, 0) + 1

        print("\n=== [TEXT] NON-BLACK COLORS IN PDF ===")
        for col, cnt in sorted(colors.items(), key=lambda x: -x[1]):
            matched = "<- TITLE" if any(color_match(eval(col) if col.startswith("(") else col, tc) for tc in title_colors if isinstance(eval(col) if col.startswith("(") else col, tuple)) else ""
            print(f"  {col:45s} ({cnt} chars) {matched}")

        # Group in rows
        all_sorted = sorted(chars, key=lambda c: c["top"])
        lines = [[all_sorted[0]]]
        for c in all_sorted[1:]:
            if abs(c["top"] - lines[-1][-1]["top"]) < 5:
                lines[-1].append(c)
            else:
                lines.append([c])

        print("\n=== [TEXT] UPPERCASE ROWS WITH LARGE FONT ===")
        found = 0
        for line in lines:
            text = "".join(c["text"] for c in sorted(line, key=lambda c: c["x0"]))
            size = max(c["size"] for c in line)
            top = min(c["top"] for c in line)
            x0 = min(c["x0"] for c in line)
            col = str(line[0].get("non_stroking_color"))
            clean = text.replace(" ", "").replace("->", "").replace("\u2022", "")
            if clean.isupper() and len(clean) > 1 and size >= min_size:
                flag = "OK" if x0 <= max_x0 else f"X x0={x0:.0f} too far right"
                print(f"  top={top:7.1f} x0={x0:5.1f} size={size:4.1f} col={col:35s} {flag} '{text[:45]}'")
                found += 1
        if not found:
            print("  (no titles found - try a smaller --min-size or check the colors with --debug-colors)")
        return True
    except TimeoutError:
        print("  [TIMEOUT] PDF is too large for text extraction, use --mode visual")
        return False
    finally:
        signal.alarm(0)


# VISUAL MODE  (PyMuPDF + NumPy)


def _hsv_title_mask(arr, hue_blue, hue_red_a, hue_red_b, min_sat, min_val):
    """Returns a boolean mask for pixels with title-like hues."""
    R = arr[:, :, 0].astype(np.float32) / 255
    G = arr[:, :, 1].astype(np.float32) / 255
    B = arr[:, :, 2].astype(np.float32) / 255
    Cmax = np.maximum(np.maximum(R, G), B)
    Cmin = np.minimum(np.minimum(R, G), B)
    delta = Cmax - Cmin
    S = np.where(Cmax > 0, delta / Cmax, 0)
    V = Cmax
    H = np.zeros_like(R)
    m = delta > 0
    mr = m & (Cmax == R)
    H[mr] = (60 * ((G[mr] - B[mr]) / delta[mr])) % 360
    mg = m & (Cmax == G)
    H[mg] = 60 * ((B[mg] - R[mg]) / delta[mg]) + 120
    mb = m & (Cmax == B)
    H[mb] = 60 * ((R[mb] - G[mb]) / delta[mb]) + 240
    sat_ok = (S > min_sat) & (V > min_val)
    blue_m = sat_ok & (H >= hue_blue[0]) & (H <= hue_blue[1])
    red_m = sat_ok & ((H >= hue_red_a[0]) | (H <= hue_red_b[1]))
    return blue_m | red_m


def find_titles_visual(pdf_path, zoom, stripe, min_band_h, max_band_h, min_ratio, max_left, merge_gap, hue_blue, hue_red_a, hue_red_b, min_sat, min_val, verbose=False):
    """
    Finds titles via HSV pixel analysis.
    Returns (title_tops, total_height, page_width).
    """
    doc = _open_pdf_silent(pdf_path)
    page = doc[0]
    W, H = page.rect.width, page.rect.height

    title_rows = []  # (pt_y, n_colored_px, img_width_px, leftmost_px)

    y = 0
    while y < H:
        y1 = min(y + stripe, H)
        pix = page.get_pixmap(
            matrix=fitz.Matrix(zoom, zoom),
            clip=fitz.Rect(0, y, W, y1),
            colorspace=fitz.csRGB,
        )
        arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
        mask = _hsv_title_mask(arr, hue_blue, hue_red_a, hue_red_b, min_sat, min_val)

        for px_row in range(pix.height):
            row = mask[px_row]
            n = int(row.sum())
            if n > 3:
                cols = np.where(row)[0]
                title_rows.append((y + px_row / zoom, n, pix.width, int(cols.min())))
        y = y1

    doc.close()

    if not title_rows:
        return [], H, W

    # Group in continuous bands
    bands = []
    bs, be, mw, iw, lx = title_rows[0][0], title_rows[0][0], title_rows[0][1], title_rows[0][2], title_rows[0][3]
    for pt, nw, tw, left in title_rows[1:]:
        if pt - be < 6.0:
            be = pt
            mw = max(mw, nw)
            lx = min(lx, left)
        else:
            bands.append((bs, be, mw, iw, lx))
            bs = pt
            be = pt
            mw = nw
            iw = tw
            lx = left
    bands.append((bs, be, mw, iw, lx))

    # Filter bands -> titles
    raw_tops = []
    for bs, be, mw, iw, lx in bands:
        h = be - bs
        ratio = mw / iw
        left_ratio = lx / iw
        if min_band_h <= h <= max_band_h and ratio >= min_ratio and left_ratio <= max_left:
            raw_tops.append(bs)

    # Merge titles (multirow)
    merged = []
    if raw_tops:
        merged = [raw_tops[0]]
        for t in raw_tops[1:]:
            if t - merged[-1] >= merge_gap:
                merged.append(t)

    return merged, H, W


def debug_colors_visual(pdf_path, zoom, stripe, hue_blue, hue_red_a, hue_red_b, min_sat, min_val, min_band_h, max_band_h, min_ratio, max_left):
    """Shows HUE distribution and candidate bands in visual mode."""
    doc = _open_pdf_silent(pdf_path)
    page = doc[0]
    W, H = page.rect.width, page.rect.height

    all_hues = []
    title_rows = []

    y = 0
    while y < H:
        y1 = min(y + stripe, H)
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=fitz.Rect(0, y, W, y1), colorspace=fitz.csRGB)
        arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3).astype(np.float32)
        R = arr[:, :, 0] / 255
        G = arr[:, :, 1] / 255
        B = arr[:, :, 2] / 255
        Cmax = np.maximum(np.maximum(R, G), B)
        delta = Cmax - np.minimum(np.minimum(R, G), B)
        S = np.where(Cmax > 0, delta / Cmax, 0)
        V = Cmax
        Hh = np.zeros_like(R)
        m = delta > 0
        mr = m & (Cmax == R)
        Hh[mr] = (60 * ((G[mr] - B[mr]) / delta[mr])) % 360
        mg = m & (Cmax == G)
        Hh[mg] = 60 * ((B[mg] - R[mg]) / delta[mg]) + 120
        mb = m & (Cmax == B)
        Hh[mb] = 60 * ((R[mb] - G[mb]) / delta[mb]) + 240
        colored = (S > min_sat) & (V > min_val)
        all_hues.extend(Hh[colored].flatten().tolist())

        mask = _hsv_title_mask(arr.astype(np.uint8) if arr.dtype != np.uint8 else arr, hue_blue, hue_red_a, hue_red_b, min_sat, min_val)
        # Apply on original uint8 arr
        arr_u = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
        mask = _hsv_title_mask(arr_u, hue_blue, hue_red_a, hue_red_b, min_sat, min_val)
        for px_row in range(pix.height):
            row = mask[px_row]
            n = int(row.sum())
            if n > 3:
                cols = np.where(row)[0]
                title_rows.append((y + px_row / zoom, n, pix.width, int(cols.min())))
        y = y1
    doc.close()

    print("\n=== [VISUAL] HUE DISTRIBUTION OF COLORED PIXELS ===")
    if all_hues:
        hue_arr = np.array(all_hues)
        buckets = np.histogram(hue_arr, bins=12, range=(0, 360))[0]
        labels = [
            "Red(0 deg)",
            "Orange(30 deg)",
            "Yellow(60 deg)",
            "Green-Yellow(90 deg)",
            "Green(120 deg)",
            "Green-Cyan(150 deg)",
            "Cyan(180 deg)",
            "Cyan-Blue(210 deg)",
            "Blue(240 deg)",
            "Blue-Purple(270 deg)",
            "Purple(300 deg)",
            "Violet-Red(330 deg)",
        ]
        mx = max(buckets) if buckets.max() > 0 else 1
        for label, cnt in zip(labels, buckets):
            bar = "#" * (cnt // (mx // 25 or 1))
            print(f"  {label:22s}: {cnt:7d}  {bar}")
        print(f"\n  Actual blue range: HUE {hue_blue[0]}-{hue_blue[1]} deg")
        print(f"  Actual range: HUE {hue_red_a[0]}-360 deg and 0-{hue_red_b[1]} deg")
        print("  -> If titles are not detected, adjust with --hue-blue / --hue-red")

    # Candidate bands
    bands = []
    if title_rows:
        bs, be, mw, iw, lx = title_rows[0]
        for pt, nw, tw, left in title_rows[1:]:
            if pt - be < 6.0:
                be = pt
                mw = max(mw, nw)
                lx = min(lx, left)
            else:
                bands.append((bs, be, mw, iw, lx))
                bs = pt
                be = pt
                mw = nw
                iw = tw
                lx = left
        bands.append((bs, be, mw, iw, lx))

    print(f"\n=== [VISUAL] CANDIDATE BANDS ({len(bands)} total) ===")
    ok_count = 0
    for bs, be, mw, iw, lx in bands:
        h = be - bs
        ratio = mw / iw
        lr = lx / iw
        ok_h = min_band_h <= h <= max_band_h
        ok_r = ratio >= min_ratio
        ok_l = lr <= max_left
        if ok_h:
            status = "OK TITLE" if (ok_r and ok_l) else f"SKIP ({'low ratio' if not ok_r else 'x0 too far right'})"
            print(f"  y={bs:7.0f}pt  h={h:5.1f}pt  ratio={ratio:.2f}  left={lr:.2f}  {status}")
            if ok_r and ok_l:
                ok_count += 1
    print(f"\nTotal candidate titles: {ok_count}")
    print("\nIf too many or too few, adjust:")
    print("  --min-band-h   minimum band height (default 14)")
    print("  --max-band-h   maximum band height (default 50)")
    print("  --min-ratio    fraction of colored width (default 0.08)")
    print("  --max-left     maximum fraction of x0 from the left (default 0.12)")
    print("  --hue-blue     blue hue range e.g., '195,225'")
    print("  --hue-red      red hue range e.g., '330,15'")


# AUTOMATIC MODE DETECTION


def detect_mode(pdf_path, timeout_sec=12):
    """
    Detects the optimal mode automatically:
    - Tries pdfplumber in subprocess on a sample (first 3000pt)
    - If it finds colored characters -> 'text'
    - If timeout or no colors -> 'visual'
    """
    import subprocess
    import sys
    import json

    script = """
import sys, json, pdfplumber
try:
    with pdfplumber.open(sys.argv[1]) as pdf:
        page = pdf.pages[0]
        h    = min(3000, page.height)
        crop = page.crop((0, 0, page.width, h))
        chars = crop.chars
        colored = [c for c in chars
                   if isinstance(c.get('non_stroking_color'), tuple)
                   and c['non_stroking_color'] not in [(0,0,0),(0.0,0.0,0.0)]]
        print(json.dumps({'colored': len(colored), 'total': len(chars)}))
except Exception as e:
    print(json.dumps({'colored': 0, 'total': 0, 'err': str(e)}))
"""
    try:
        r = subprocess.run([sys.executable, "-c", script, pdf_path], capture_output=True, text=True, timeout=timeout_sec)
        if r.stdout.strip():
            d = json.loads(r.stdout.strip())
            return "text" if d.get("colored", 0) > 5 else "visual"
        return "visual"
    except Exception:
        return "visual"


# MAIN FUNCTION


def paginate_pdf(
    input_path,
    output_path,
    mode="auto",
    clean=CLEAN_DARK_BARS,
    clean_dark_fill=CLEAN_DARK_FILL,
    clean_min_width=CLEAN_MIN_WIDTH,
    clean_min_height=CLEAN_MIN_HEIGHT,
    # text
    min_title_size=MIN_TITLE_SIZE,
    merge_gap=TITLE_MERGE_GAP,
    max_x0=MAX_TITLE_X0,
    title_colors=None,
    # visual
    zoom=VISUAL_ZOOM,
    stripe=VISUAL_STRIPE,
    min_band_h=VISUAL_MIN_BAND_H,
    max_band_h=VISUAL_MAX_BAND_H,
    min_ratio=VISUAL_MIN_RATIO,
    max_left=VISUAL_MAX_LEFT,
    hue_blue=HUE_BLUE,
    hue_red_a=HUE_RED_A,
    hue_red_b=HUE_RED_B,
    min_sat=MIN_SAT,
    min_val=MIN_VAL,
    # pagination
    padding=TITLE_PADDING,
    max_page_ratio=MAX_PAGE_RATIO,
    min_last_page=MIN_LAST_PAGE,
    a4_height=A4_HEIGHT_PT,
    verbose=True,
):

    if title_colors is None:
        title_colors = list(TITLE_COLORS)

    if not os.path.exists(input_path):
        raise FileNotFoundError(f"File not found: {input_path}")

    # Clean dark bars if requested (annotations left by text selection)
    working_path = input_path
    _tmp_clean = None
    if clean:
        import tempfile

        _tmp_clean = tempfile.mktemp(suffix="_clean.pdf")
        n_removed = clean_dark_bars(input_path, _tmp_clean, dark_fill=clean_dark_fill, min_width=clean_min_width, min_height=clean_min_height)
        if verbose and n_removed > 0:
            print(f"[INFO] Clean: {n_removed} dark bars removed")
        elif verbose:
            print("[INFO] Clean: no dark bars found")
        working_path = _tmp_clean

    # Detect mode
    if mode == "auto":
        mode = detect_mode(working_path)
        if verbose:
            print(f"[INFO] Mode detected automatically: {mode.upper()}")
    else:
        if verbose:
            print(f"[INFO] Mode forced: {mode.upper()}")

    # Extraction of titles
    if mode == "text":
        title_tops, total_height, page_width = find_titles_text(working_path, min_title_size, merge_gap, max_x0, title_colors)
        if title_tops is None:
            if verbose:
                print("[WARN] Text mode failed (timeout), switching to visual...")

            mode = "visual"

    if mode == "visual":
        title_tops, total_height, page_width = find_titles_visual(
            working_path,
            zoom,
            stripe,
            min_band_h,
            max_band_h,
            min_ratio,
            max_left,
            merge_gap,
            hue_blue,
            hue_red_a,
            hue_red_b,
            min_sat,
            min_val,
            verbose=verbose,
        )

    if verbose:
        print(f"[INFO] PDF: {page_width:.0f}x{total_height:.0f}pt " f"({page_width / 72 * 25.4:.0f}x{total_height / 72 * 25.4:.0f}mm)")
        print(f"[INFO] Titles detected: {len(title_tops)}")

    if not title_tops:
        print("\n[ERROR] No titles found. Try:")
        if mode == "text":
            print("  --min-size 10         lower font threshold")
            print("  --mode visual         switch to visual mode")
            print("  --debug-colors        analyze present colors")
        else:
            print("  --debug-colors        analyze colors and bands")
            print("  --min-band-h 8        lower minimum band height")
            print("  --min-ratio 0.05      lower width threshold")
            print("  --hue-blue '195,225'  check blue hue range")
            print("  --hue-red '330,15'    check red hue range")
        return 0

    # Compute pages and output
    pages = compute_pages(title_tops, total_height, padding, max_page_ratio, min_last_page, a4_height, working_path=working_path, zoom=zoom)

    if verbose:
        print(f"[INFO] Pages to generate: {len(pages)}\n")

    write_pdf(working_path, output_path, pages, page_width, verbose)

    # Remove temporary cleanup file
    if _tmp_clean and os.path.exists(_tmp_clean):
        os.remove(_tmp_clean)

    if verbose:
        print(f"\n[OK] Saved: {output_path}")

    return len(pages)


# CLI


def parse_hue(s):
    """Parsing '195,225' -> (195, 225)"""
    parts = [float(x.strip()) for x in s.split(",")]
    if len(parts) != 2:
        raise argparse.ArgumentTypeError("Format: 'min,max' e.g., '195,225'")
    return tuple(parts)


def parse_colors(s):
    """Parsing 'r,g,b;r,g,b' -> list of tuples"""
    result = []
    for entry in s.split(";"):
        parts = [float(x.strip()) for x in entry.strip().split(",")]
        if len(parts) != 3:
            raise argparse.ArgumentTypeError(f"Color '{entry}' is invalid, need r,g,b")
        result.append(tuple(parts))
    return result


def main():
    p = argparse.ArgumentParser(
        description="Automatic PDF pagination from tablet notes.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("input", help="Source PDF (vertical scroll)")
    p.add_argument("-o", "--output", help="Output PDF (default: <input>_paginated.pdf)")

    g0 = p.add_argument_group("Mode")
    g0.add_argument("--mode", choices=["auto", "text", "visual"], default="auto", help="'auto'=automatic detection, 'text'=force text mode, 'visual'=force visual mode")
    g0.add_argument("--debug-colors", action="store_true", help="Show present colors/bands without generating output")

    g1 = p.add_argument_group("Text Mode")
    g1.add_argument("--min-size", type=float, default=MIN_TITLE_SIZE, help="Minimum font size for title")
    g1.add_argument("--max-x0", type=float, default=MAX_TITLE_X0, help="Maximum left margin (pt) for aligned title")
    g1.add_argument("--colors", type=parse_colors, default=None, help='Custom title colors "r,g,b;r,g,b" (values 0-1)')

    g2 = p.add_argument_group("Visual Mode")
    g2.add_argument("--zoom", type=float, default=VISUAL_ZOOM, help="Rasterization factor (higher = more precise but slower)")
    g2.add_argument("--min-band-h", type=float, default=VISUAL_MIN_BAND_H, help="Minimum height of colored band (pt) to be considered a title")
    g2.add_argument("--max-band-h", type=float, default=VISUAL_MAX_BAND_H, help="Maximum height of band (pt) - beyond = image, not title")
    g2.add_argument("--min-ratio", type=float, default=VISUAL_MIN_RATIO, help="Minimum fraction of width with colored pixels")
    g2.add_argument("--max-left", type=float, default=VISUAL_MAX_LEFT, help="Maximum fraction of width for the leftmost pixel")
    g2.add_argument("--hue-blue", type=parse_hue, default=HUE_BLUE, metavar="'MIN,MAX'", help=f"Blue hue range in degrees 0-360 (default '{HUE_BLUE[0]},{HUE_BLUE[1]}')")
    g2.add_argument("--hue-red", type=parse_hue, default=None, metavar="'WRAP,MAX'", help="Red hue range: 'wrap,max' where wrap=start(>=0) max=end(<=360). " f"Default '{HUE_RED_A[0]},{HUE_RED_B[1]}'")
    g2.add_argument("--min-sat", type=float, default=MIN_SAT, help="Minimum saturation of colored pixels")
    g2.add_argument("--min-val", type=float, default=MIN_VAL, help="Minimum value (luminosity) of colored pixels")

    g3 = p.add_argument_group("Pagination")
    g3.add_argument("--padding", type=float, default=TITLE_PADDING, help="Space (pt) before each title in the cut")
    g3.add_argument("--merge-gap", type=float, default=TITLE_MERGE_GAP, help="Minimum gap (pt) between distinct titles (merges multi-line headings)")
    g3.add_argument("--max-ratio", type=float, default=MAX_PAGE_RATIO, help="Maximum height of page as multiple of A4")
    g3.add_argument("--min-last", type=float, default=MIN_LAST_PAGE, help="Minimum height (pt) of last page before merging")
    g3.add_argument("--no-clean", action="store_true", help="Disable automatic cleanup of dark bars")
    g3.add_argument("--clean-fill", type=float, default=CLEAN_DARK_FILL, help="Fill threshold (0-1) to consider a rectangle 'dark'")
    g3.add_argument("--clean-min-w", type=float, default=CLEAN_MIN_WIDTH, help="Minimum width of bar as fraction of page")
    g3.add_argument("--clean-min-h", type=float, default=CLEAN_MIN_HEIGHT, help="Minimum height of bar (pt) to be removed")
    g3.add_argument("-q", "--quiet", action="store_true", help="No output")

    args = p.parse_args()

    # Colors for text mode (pdfplumber)
    title_colors = args.colors if args.colors else list(TITLE_COLORS)

    # Red Hue
    if args.hue_red:
        hue_red_a = (args.hue_red[0], 360)
        hue_red_b = (0, args.hue_red[1])
    else:
        hue_red_a = HUE_RED_A
        hue_red_b = HUE_RED_B

    # Output path
    out_path = args.output or (os.path.splitext(args.input)[0] + "_paginated.pdf")

    # Debug mode
    if args.debug_colors:
        mode = args.mode if args.mode != "auto" else detect_mode(args.input)
        print(f"[INFO] Mode: {mode.upper()}")
        if mode == "text":
            ok = debug_colors_text(args.input, args.min_size, args.max_x0, title_colors)
            if not ok:
                mode = "visual"
        if mode == "visual":
            debug_colors_visual(
                args.input,
                args.zoom,
                int(VISUAL_STRIPE),
                args.hue_blue,
                hue_red_a,
                hue_red_b,
                args.min_sat,
                args.min_val,
                args.min_band_h,
                args.max_band_h,
                args.min_ratio,
                args.max_left,
            )
        return

    paginate_pdf(
        input_path=args.input,
        output_path=out_path,
        mode=args.mode,
        clean=not args.no_clean,
        clean_dark_fill=args.clean_fill,
        clean_min_width=args.clean_min_w,
        clean_min_height=args.clean_min_h,
        min_title_size=args.min_size,
        merge_gap=args.merge_gap,
        max_x0=args.max_x0,
        title_colors=title_colors,
        zoom=args.zoom,
        min_band_h=args.min_band_h,
        max_band_h=args.max_band_h,
        min_ratio=args.min_ratio,
        max_left=args.max_left,
        hue_blue=args.hue_blue,
        hue_red_a=hue_red_a,
        hue_red_b=hue_red_b,
        min_sat=args.min_sat,
        min_val=args.min_val,
        padding=args.padding,
        max_page_ratio=args.max_ratio,
        min_last_page=args.min_last,
        verbose=not args.quiet,
    )


if __name__ == "__main__":
    main()
