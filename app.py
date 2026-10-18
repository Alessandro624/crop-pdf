#!/usr/bin/env python3
"""
app.py - Multi-tab, security-hardened Flask server for the PDF pagination tool.
"""

import base64
import os
import re
import sys
import tempfile
import threading
import time
import uuid
from flask import Flask, after_this_request, jsonify, request, send_file, send_from_directory  # type: ignore
from werkzeug.utils import secure_filename  # type: ignore

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import crop_pdf as pp  # noqa: E402

try:
    import pymupdf as fitz  # type: ignore
except ImportError:
    import fitz  # type: ignore

app = Flask(__name__, static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16MB max

UPLOAD_DIR = tempfile.mkdtemp(prefix="pdfpag_")
ZOOM_PREVIEW = 1.5
STRIPE_H = 800

JOBS_LOCK = threading.Lock()
JOBS = {}
MAX_ACTIVE_JOBS = 6
JOB_ID_RE = re.compile(r"^[a-f0-9]{32}$")


def _remove_job_files(job):
    """Physically removes the files associated with a session from /tmp."""
    if not job:
        return
    for key in ("pdf_path", "clean_path"):
        path = job.get(key)
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass


def _register_job(job_data):
    """Registers a new tab and removes the oldest ones if MAX_ACTIVE_JOBS is exceeded."""
    job_id = uuid.uuid4().hex
    job_data["created_at"] = time.time()
    with JOBS_LOCK:
        while len(JOBS) >= MAX_ACTIVE_JOBS:
            oldest_id = min(JOBS, key=lambda k: JOBS[k]["created_at"])
            old_job = JOBS.pop(oldest_id, None)
            _remove_job_files(old_job)
        JOBS[job_id] = job_data
    return job_id


def _get_job(job_id):
    """Validates the job_id against Path Traversal/Injection attacks and returns the tab data."""
    if not isinstance(job_id, str) or not JOB_ID_RE.match(job_id):
        return None
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job:
            job["created_at"] = time.time()
        return job


def render_strips(pdf_path, zoom=ZOOM_PREVIEW, stripe=STRIPE_H):
    """Renders the PDF into Base64-encoded PNG strips for preview."""
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


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/upload", methods=["POST"])
def upload():
    """Uploads the PDF, removes any dark bars, calculates the cuts, and creates the job_id."""
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "No file selected"}), 400

    if not f.filename.lower().endswith(".pdf"):
        return jsonify({"error": "File must be a PDF"}), 400

    safe_orig = secure_filename(f.filename) or "input.pdf"
    orig_stem = os.path.splitext(safe_orig)[0] or "output"

    fd_in, raw_path = tempfile.mkstemp(prefix="in_", suffix=".pdf", dir=UPLOAD_DIR)
    os.close(fd_in)
    f.save(raw_path)

    page_format = request.form.get("page_format", "a4").lower()
    cut_mode = request.form.get("cut_mode", "smart").lower()

    if cut_mode != "manual" and page_format in pp.PAGE_FORMATS:
        fd_cl, clean_path = tempfile.mkstemp(prefix="cl_", suffix=".pdf", dir=UPLOAD_DIR)
        os.close(fd_cl)
        n_removed = pp.clean_dark_bars(raw_path, clean_path)
        smart_flag = cut_mode == "smart"
        cut_points, W, H = pp.find_format_cuts(clean_path, page_format=page_format, smart=smart_flag)
    else:
        clean_path = raw_path
        n_removed = 0
        cut_points = []
        W, H = 0, 0

    strips, W_render, H_render = render_strips(clean_path)
    W = W or W_render
    H = H or H_render

    job_id = _register_job(
        {
            "pdf_path": raw_path,
            "clean_path": clean_path,
            "total_height": H,
            "page_width": W,
            "orig_name": orig_stem,
            "auto_cuts": list(cut_points),
            "page_format": page_format if page_format in pp.PAGE_FORMATS else "a4",
        }
    )

    return jsonify(
        {
            "job_id": job_id,
            "total_height": H,
            "page_width": W,
            "zoom_preview": ZOOM_PREVIEW,
            "strip_h_pt": STRIPE_H,
            "cut_points": cut_points,
            "bars_removed": n_removed,
            "page_format": page_format,
            "cut_mode": cut_mode,
            "orig_name": orig_stem,
            "strips": strips,
        }
    )


@app.route("/recalculate", methods=["POST"])
def recalculate():
    """Recalculates cut points on the fly when the format changes, without re-uploading the PDF."""
    data = request.get_json(silent=True) or {}
    job = _get_job(data.get("job_id"))
    if not job or not os.path.exists(job["clean_path"]):
        return jsonify({"error": "Session expired or no PDF loaded"}), 400

    page_format = str(data.get("page_format", "a4")).lower()
    cut_mode = str(data.get("cut_mode", "smart")).lower()

    if cut_mode == "manual" or page_format not in pp.PAGE_FORMATS:
        cuts = []
    else:
        cuts, _, _ = pp.find_format_cuts(
            job["clean_path"],
            page_format=page_format,
            smart=(cut_mode == "smart"),
        )

    with JOBS_LOCK:
        job["auto_cuts"] = list(cuts)
        job["page_format"] = page_format

    return jsonify({"cut_points": cuts, "page_format": page_format, "cut_mode": cut_mode})


@app.route("/paginate", methods=["POST"])
def paginate():
    """Receives the final cuts, updates the ML weights, and returns the compressed PDF."""
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Invalid JSON payload"}), 400

    job = _get_job(data.get("job_id"))
    if not job or not os.path.exists(job["clean_path"]):
        return jsonify({"error": "No PDF loaded or session expired"}), 400

    raw_cuts = data.get("cut_points", [])
    if not isinstance(raw_cuts, list):
        return jsonify({"error": "cut_points must be an array"}), 400

    try:
        cut_points = [float(cp) for cp in raw_cuts]
    except (ValueError, TypeError):
        return jsonify({"error": "cut_points must contain valid numbers"}), 400

    H = float(job["total_height"])
    W = float(job["page_width"])

    cut_points = sorted(set(cp for cp in cut_points if 0.0 < cp < H))

    learned = False
    if job.get("auto_cuts"):
        learned = pp.update_weights_from_feedback(
            job["clean_path"],
            job["auto_cuts"],
            cut_points,
            page_format=job.get("page_format", "a4"),
        )

    out_name = str(data.get("name", job.get("orig_name", "output"))).strip() or "output"
    out_name = "".join(c for c in out_name if c.isalnum() or c in " _-.").rstrip(".") or "output"

    boundaries = cut_points + [H]
    pages = []
    start = 0.0
    for end in boundaries:
        if end - start > 10.0:
            pages.append((start, end))
            start = end

    fd_out, out_path = tempfile.mkstemp(prefix="out_", suffix=".pdf", dir=UPLOAD_DIR)
    os.close(fd_out)

    pp.write_pdf(job["clean_path"], out_path, pages, W, verbose=False)

    @after_this_request
    def _cleanup_output(response):
        try:
            if os.path.exists(out_path):
                os.remove(out_path)
        except OSError:
            pass
        response.headers["X-Feedback-Learned"] = "1" if learned else "0"
        return response

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
