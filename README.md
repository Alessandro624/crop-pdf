# PDF Paginator

A tool for automatically paginating vertical-scroll PDFs (like tablet notes) into standard-sized pages.

## Features

- **Automatic Title Detection**: Detects titles using either text metadata (pdfplumber) or visual pixel analysis (PyMuPDF + NumPy)
- **Interactive Web UI**: Drag-and-drop PDF upload with visual cut point editing
- **Smart Pagination**: DP algorithm groups sections to minimize deviation from A4 size
- **Dark Bar Removal**: Automatically cleans annotation artifacts from PDFs
- **Multiple Modes**: Auto-detect, force text mode, or force visual mode

## Installation

```bash
pip install -r requirements.txt
```

Requirements:

- Python 3.7+
- flask
- pdfplumber
- pymupdf
- numpy

## Usage

### Web Interface

```bash
python3 app.py
```

Then open <http://localhost:5000> in your browser.

1. Drag and drop a PDF or click to browse
2. Toggle "Auto cuts" for automatic cut point detection
3. Click the PDF to add manual cut points
4. Drag cut lines to reposition them
5. Download the paginated PDF

### Command Line

```bash
# Automatic mode (detects best method)
python3 crop_pdf.py input.pdf

# Specify output name
python3 crop_pdf.py input.pdf -o output.pdf

# Force text mode
python3 crop_pdf.py input.pdf --mode text

# Force visual mode
python3 crop_pdf.py input.pdf --mode visual

# Debug colors (see what the tool detects)
python3 crop_pdf.py input.pdf --debug-colors
```

## Configuration

### Text Mode Options

- `--min-size`: Minimum font size for title detection (default: 13.5)
- `--max-x0`: Maximum left margin for title alignment (default: 50.0 pt)
- `--colors`: Custom title colors as "r,g,b;r,g,b" (default: blue and red)

### Visual Mode Options

- `--zoom`: Rasterization factor (default: 1.5)
- `--min-band-h`: Minimum colored band height (default: 14.0 pt)
- `--max-band-h`: Maximum colored band height (default: 50.0 pt)
- `--min-ratio`: Minimum width fraction with colored pixels (default: 0.08)
- `--hue-blue`: Blue hue range as "min,max" (default: "195,225")
- `--hue-red`: Red hue range as "wrap,max" (default: "330,15")

### Pagination Options

- `--padding`: Space before each title in the cut (default: 14.0 pt)
- `--merge-gap`: Minimum gap between titles (default: 60.0 pt)
- `--max-ratio`: Maximum page height as multiple of A4 (default: 1.15)
- `--min-last`: Minimum height of last page before merging (default: 120.0 pt)

## How It Works

1. **Clean**: Removes dark annotation bars from the PDF
2. **Detect Mode**: Analyzes the PDF to choose text or visual mode
3. **Find Titles**: Identifies title positions using color/size analysis
4. **Find Cut Points**: Searches for optimal whitespace lines near titles
5. **Compute Pages**: Uses DP to group sections into A4-sized pages
6. **Generate PDF**: Creates the output PDF with proper page breaks

## Testing

```bash
python3 -m pytest tests/ -v
```
