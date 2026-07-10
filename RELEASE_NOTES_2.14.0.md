<!-- release-title: v2.14.0 — Read PDFs and glob patterns with :read -->
**TL;DR:** Aida can now use **PDFs as learning material** and **shell-style globs** — still fully user-directed, still honest. **v2.14.0** adds `:read ~/docs/*.pdf`, page-marked PDF text extraction (PyMuPDF), optional **Tesseract OCR** for scanned pages, and glob bundles like `*.py` — all through the existing `:more` paging path. Non-PDF reads are unchanged.

## Why this release matters

v2.13 gave you setup clarity and voice control. **v2.14 completes the file-reading story** for real work: manuals, papers, specs, and multi-file codebases — without the model pretending to browse on its own.

- **PDFs as a learning tool** — `:read report.pdf` extracts real text (page by page), with honest notices for image-only pages and truncation.
- **Globs that actually work** — `:read ~/project/*.py` attaches matched files in one bundle (capped, sorted, paged).
- **Same honesty model** — runtime reads; model reasons; unseen pages/chunks carry explicit notices; encrypted or empty PDFs are refused plainly.

## What's new in 2.14.0

### PDF reading (`:read` on `.pdf`)

- **PyMuPDF** extracts embedded text deterministically into page-marked blocks (`--- Page N ---`).
- **Document profile header** — page counts, extraction method, title/author when present, image-only page list.
- **Optional OCR** for scanned/sparse pages when Tesseract is installed (never invented — failures stay labeled).
- **Encrypted PDFs** refused with a plain error (no password guessing).
- **Large PDFs** use existing `:more` char paging (up to `pdf_max_pages` extracted, default 500).
- **Config knobs** in `config.yaml`: `pdf_reader_enabled`, `pdf_max_pages`, `pdf_ocr_enabled`, etc.

### Glob expansion (`:read` with `*`, `?`, `[`)

- Patterns like `~/NetVendor/*.py` expand to matched files when the literal path does not exist.
- **Literal paths still win** — a file literally named `foo*bar.txt` is not glob-expanded.
- **0 matches** → honest error; **1 match** → same as reading that file; **N matches** → bounded bundle (default cap 20 files).

### Docs

- README updated for PDF install steps and `:read` capabilities.

## Upgrade

```bash
cd continuous-ai   # or your clone path
git pull
bash setup.sh      # re-runs pip install -r requirements.txt (now includes pymupdf)
bash run.sh
```

**Required for PDF support** (added to `requirements.txt`):

```bash
./.venv/bin/pip install -r requirements.txt
```

This installs **`pymupdf>=1.24.0`** into your project venv. Born-digital PDFs work with that alone.

**Optional — scanned / image-only PDF pages** (OCR):

```bash
# macOS
brew install tesseract
./.venv/bin/pip install pytesseract pillow

# Debian/Ubuntu
sudo apt install tesseract-ocr
./.venv/bin/pip install pytesseract pillow
```

If Tesseract is not installed, sparse pages are marked `[NO EXTRACTABLE TEXT]` — Aida will not guess their contents.

**New config keys** (all optional; sensible defaults):

```yaml
pdf_reader_enabled: true
pdf_max_pages: 500
pdf_min_chars_per_page: 25
pdf_min_extractable_ratio: 0.02
pdf_ocr_enabled: true
pdf_ocr_dpi_scale: 2.0
```

Try in chat:

```
:read ~/Documents/manual.pdf
:read ~/project/src/*.py
read ~/report.pdf what are the action items?
```

## Tests

- Filereader suite **26/26 green** (adds glob + PDF extraction, encryption refusal, paging, and config-disable cases).
- Confabulation / honesty model unchanged — PDF text is user-attached content, not model retrieval.

**Full changes:** `v2.13.0..v2.14.0`
