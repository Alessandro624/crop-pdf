# PDF Paginator

A fast, multi-tab web application and CLI tool for paginating vertical-scroll PDFs (such as tablet notes) into standard page formats with smart whitespace detection and online preference learning.

## Features

- **Format-Based Pagination**: Supports A4/A3 Vertical (`1:1.414`), Tablet 4:3 (`1:1.333`), Vertical 16:9 (`1:1.778`), A4/A3 Landscape (`1:0.707`), and Square (`1:1`).
- **Smart Whitespace Snap**: Analyzes grayscale pixel density (via PyMuPDF + NumPy) within a tolerance window around the target format height to avoid cutting through text, formulas, or diagrams.
- **Online Preference Learning (Human Feedback)**: Automatically adjusts its internal scoring weights whenever you manually drag and refine proposed cuts in the web UI.
- **Multi-Tab & Security Hardened**: Isolated per-tab `job_id` sessions with thread-safe LRU eviction and automatic `/tmp` cleanup.
- **Maximum PDF Compression**: Deduplicates shared streams (`garbage=4`) and compresses streams, images, and fonts (`deflate=True`, `deflate_images=True`, `deflate_fonts=True`).
- **Dark Bar Removal**: Automatically cleans dark selection/annotation bars left in exported notes.

## Installation

```bash
pip install -r requirements.txt
```

Requirements:

- Python 3.10+
- `flask`
- `pymupdf`
- `numpy`

## Usage

### Web Interface

```bash
python3 app.py
```

Open [http://localhost:5000](http://localhost:5000) in your browser:

1. Drag and drop a PDF or click to browse (multiple browser tabs are supported independently).
2. Choose your **Target Format** (A4, Tablet 4:3, 16:9, etc.) and **Cut Mode** (*Smart Snap*, *Exact Format Height*, or *Manual Only*).
3. Click on the preview canvas to add cuts, or drag existing cut lines to reposition them.
4. Click **Download Paginated PDF** (any manual adjustments to auto-cuts will train the Smart Snap weights).

### Command Line

```bash
# Default: A4 format with Smart Whitespace Snap
python3 crop_pdf.py input.pdf

# Specify output file and target format
python3 crop_pdf.py input.pdf -o output.pdf --format tablet_4_3

# Cut at exact mathematical format height (disable smart whitespace search)
python3 crop_pdf.py input.pdf --format a4 --exact

# Disable dark bar cleanup
python3 crop_pdf.py input.pdf --no-clean
```

### CLI Options

- `-o, --output`: Output PDF path (default: `<input>_paginated.pdf`)
- `-f, --format`: Target format aspect ratio (`a4`, `tablet_4_3`, `16_9`, `a4_land`, `square`)
- `--exact`: Cut at exact format height without searching for whitespace
- `--no-clean`: Skip dark annotation bar removal
- `-q, --quiet`: Suppress console output

## Testing & Linting

```bash
python3 -m pytest tests/ -v
flake8 .
```
