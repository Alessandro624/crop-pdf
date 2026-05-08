#!/usr/bin/env python3
"""
app.py - Flask server for the PDF pagination tool.
Run with: python app.py
Then open http://localhost:5000 in your browser
"""

import os
import sys
import base64
import tempfile
from flask import Flask, request, jsonify, send_file, send_from_directory  # type: ignore
from werkzeug.utils import secure_filename

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import crop_pdf as pp  # noqa: E402
import fitz  # noqa: E402

app = Flask(__name__, static_folder="static")
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max

# Global Session State (single-user)
session = {
    "pdf_path": None,
    "clean_path": None,
    "total_height": 0,
    "page_width": 0,
}

UPLOAD_DIR = tempfile.mkdtemp(prefix="pdfpag_")
ZOOM_PREVIEW = 1.5
STRIPE_H = 800


# Utility


def render_strips(pdf_path, zoom=ZOOM_PREVIEW, stripe=STRIPE_H):
    """Renders the PDF in strip base64 PNG for the preview."""
    fitz.TOOLS.mupdf_display_errors(False)
    fitz.TOOLS.mupdf_display_warnings(False)
    doc = fitz.open(pdf_path)
    page = doc[0]
    W, H = page.rect.width, page.rect.height
    strips = []
    y = 0
    while y < H:
        y1 = min(y + stripe, H)
        pix = page.get_pixmap(
            matrix=fitz.Matrix(zoom, zoom),
            clip=fitz.Rect(0, y, W, y1),
            colorspace=fitz.csRGB,
        )
        b64 = base64.b64encode(pix.tobytes("png")).decode()
        strips.append(
            {
                "y_start": y,
                "y_end": y1,
                "height_px": pix.height,
                "b64": b64,
            }
        )
        y = y1
    doc.close()
    return strips, W, H


# Endpoints


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/upload", methods=["POST"])
def upload():
    """Processes the uploaded PDF, cleans it, analyzes titles, and returns strips and cut points."""
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    f = request.files["file"]
    if f.filename == '':
        return jsonify({"error": "No file selected"}), 400

    # Validate file extension
    if not f.filename.lower().endswith('.pdf'):
        return jsonify({"error": "File must be a PDF"}), 400

    name = secure_filename(f.filename or "input.pdf")
    path = os.path.join(UPLOAD_DIR, name)
    f.save(path)

    auto_cut = request.form.get("auto_cut", "1").lower() in ("1", "true", "yes", "on")

    if auto_cut:
        # Clean PDF (remove dark bars)
        clean_path = os.path.join(UPLOAD_DIR, "clean.pdf")
        n_removed = pp.clean_dark_bars(path, clean_path)

        # Title detection
        mode = pp.detect_mode(clean_path)
        title_tops, total_height, page_width = (
            pp.find_titles_visual(
                clean_path,
                pp.VISUAL_ZOOM,
                pp.VISUAL_STRIPE,
                pp.VISUAL_MIN_BAND_H,
                pp.VISUAL_MAX_BAND_H,
                pp.VISUAL_MIN_RATIO,
                pp.VISUAL_MAX_LEFT,
                pp.TITLE_MERGE_GAP,
                pp.HUE_BLUE,
                pp.HUE_RED_A,
                pp.HUE_RED_B,
                pp.MIN_SAT,
                pp.MIN_VAL,
            )
            if mode == "visual"
            else pp.find_titles_text(
                clean_path,
                pp.MIN_TITLE_SIZE,
                pp.TITLE_MERGE_GAP,
                pp.MAX_TITLE_X0,
                pp.TITLE_COLORS,
            )
        )
        title_tops = title_tops or []

        # Compute cut points with white space detection
        cut_points = (
            pp.find_cut_points_batch(
                clean_path,
                title_tops,
                search_range=pp.TITLE_PADDING * 4,
                zoom=pp.VISUAL_ZOOM,
            )
            if title_tops
            else []
        )
    else:
        # Manual mode: no clean, no auto titles/cuts
        clean_path = path
        n_removed = 0
        mode = "manual"
        title_tops = []
        cut_points = []
        total_height = 0
        page_width = 0

    # Render strip preview
    strips, W, H = render_strips(clean_path)
    if not total_height:
        total_height = H
    if not page_width:
        page_width = W

    # Save in session
    session["pdf_path"] = path
    session["clean_path"] = clean_path
    session["total_height"] = total_height
    session["page_width"] = page_width
    session["orig_name"] = os.path.splitext(name)[0]

    return jsonify(
        {
            "total_height": total_height,
            "page_width": page_width,
            "zoom_preview": ZOOM_PREVIEW,
            "strip_h_pt": STRIPE_H,
            "cut_points": cut_points,
            "title_tops": title_tops,
            "bars_removed": n_removed,
            "mode": mode,
            "auto_cut": auto_cut,
            "orig_name": session["orig_name"],
            "strips": strips,
        }
    )


@app.route("/preview")
def preview():
    """Serve HTML page with PDF.js viewer for the given PDF."""
    pdf_name = request.args.get("pdf")
    if not pdf_name:
        return jsonify({"error": "Missing PDF parameter"}), 400

    pdf_path = os.path.join(UPLOAD_DIR, secure_filename(pdf_name))
    if not os.path.exists(pdf_path):
        return jsonify({"error": "PDF not found"}), 404

    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <title>PDF Preview</title>
        <script src="https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.min.js"></script>
        <script src="/static/js/pdf-viewer.js"></script>
        <style>
            body {{ margin: 0; padding: 20px; font-family: sans-serif; }}
            #viewer {{ max-width: 800px; margin: 0 auto; }}
        </style>
    </head>
    <body>
        <div id="viewer"></div>
        <script>
            const viewer = new PDFViewer('viewer');
            viewer.loadPDF('/static/uploads/{pdf_name}');
        </script>
    </body>
    </html>
    """
    return html


@app.route("/paginate", methods=["POST"])
def paginate():
    """Receives the final cut points and generates the paginated PDF."""
    if not session.get("clean_path"):
        return jsonify({"error": "No PDF loaded"}), 400

    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Invalid JSON payload"}), 400
    except Exception:
        return jsonify({"error": "Invalid JSON payload"}), 400

    cut_points = data.get("cut_points", [])
    if not isinstance(cut_points, list):
        return jsonify({"error": "cut_points must be an array"}), 400

    # Validate cut points are numbers
    try:
        cut_points = [float(cp) for cp in cut_points]
    except (ValueError, TypeError):
        return jsonify({"error": "cut_points must contain valid numbers"}), 400

    cut_points = sorted(set(cut_points))  # Remove duplicates and sort
    cut_points = sorted(data.get("cut_points", []))
    out_name = data.get("name", session.get("orig_name", "output")).strip() or "output"
    # Sanitize file name
    out_name = "".join(c for c in out_name if c.isalnum() or c in " _-.")
    out_name = out_name.rstrip(".") or "output"

    clean_path = session["clean_path"]
    H = session["total_height"]
    W = session["page_width"]

    # Build page boundaries: start at 0, then cut points, then end at total height
    boundaries = cut_points + [H]
    pages = []
    start = 0.0
    for end in boundaries:
        if end - start > 10:
            pages.append((start, end))
            start = end

    # Generate compressed PDF
    out_path = os.path.join(UPLOAD_DIR, f"{out_name}.pdf")
    fitz.TOOLS.mupdf_display_errors(False)
    src = fitz.open(clean_path)
    out = fitz.open()
    for y0, y1 in pages:
        h = y1 - y0
        pg = out.new_page(width=W, height=h)
        pg.show_pdf_page(
            fitz.Rect(0, 0, W, h),
            src,
            0,
            clip=fitz.Rect(0, y0, W, y1),
        )
    # Save with maximum compression
    out.save(out_path, garbage=4, deflate=True, clean=True)
    out.close()
    src.close()

    return send_file(
        out_path,
        as_attachment=True,
        download_name=f"{out_name}.pdf",
        mimetype="application/pdf",
    )


if __name__ == "__main__":
    os.makedirs("static", exist_ok=True)
    print("\nPDF Paginator started on http://localhost:5000\n")
    app.run(debug=False, port=5000)
