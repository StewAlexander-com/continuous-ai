"""pdfreader — deterministic PDF text extraction for :read.

Uses PyMuPDF (fitz) for native text extraction and page rendering. When a page
has little or no embedded text (typical of scanned PDFs), optional OCR via
Tesseract is attempted if available — never invented.

PyMuPDF is AGPL-3.0; Seedling (MIT) uses it as an optional runtime library for
user-directed file reads only. OCR requires the external ``tesseract`` binary
plus ``pytesseract`` + ``Pillow`` (soft dependencies).
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_PDF_MAX_PAGES = 500
DEFAULT_PDF_MIN_CHARS_PER_PAGE = 25
DEFAULT_PDF_MIN_EXTRACTABLE_RATIO = 0.02
DEFAULT_PDF_OCR_DPI_SCALE = 2.0


@dataclass(frozen=True)
class PdfOptions:
    enabled: bool = True
    max_pages: int = DEFAULT_PDF_MAX_PAGES
    min_chars_per_page: int = DEFAULT_PDF_MIN_CHARS_PER_PAGE
    min_extractable_ratio: float = DEFAULT_PDF_MIN_EXTRACTABLE_RATIO
    ocr_enabled: bool = True
    ocr_dpi_scale: float = DEFAULT_PDF_OCR_DPI_SCALE


def pdf_options_from_config(config: dict[str, Any] | None) -> PdfOptions:
    c = config or {}
    return PdfOptions(
        enabled=bool(c.get("pdf_reader_enabled", True)),
        max_pages=int(c.get("pdf_max_pages", DEFAULT_PDF_MAX_PAGES)),
        min_chars_per_page=int(c.get("pdf_min_chars_per_page", DEFAULT_PDF_MIN_CHARS_PER_PAGE)),
        min_extractable_ratio=float(c.get("pdf_min_extractable_ratio",
                                          DEFAULT_PDF_MIN_EXTRACTABLE_RATIO)),
        ocr_enabled=bool(c.get("pdf_ocr_enabled", True)),
        ocr_dpi_scale=float(c.get("pdf_ocr_dpi_scale", DEFAULT_PDF_OCR_DPI_SCALE)),
    )


def _import_fitz():
    try:
        import fitz  # pymupdf
        return fitz
    except ImportError:
        return None


def _ocr_runtime_ready() -> bool:
    if not shutil.which("tesseract"):
        return False
    try:
        import pytesseract  # noqa: F401
        from PIL import Image  # noqa: F401
        return True
    except ImportError:
        return False


def _ocr_page(page, fitz, *, scale: float) -> str:
    try:
        import pytesseract
        from PIL import Image

        matrix = fitz.Matrix(scale, scale)
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        return pytesseract.image_to_string(img).strip()
    except Exception:
        return ""


def _page_body(page, fitz, *, opts: PdfOptions) -> tuple[str, str]:
    """Return (page_text, method_tag) where method_tag is text|ocr|image-only."""
    native = (page.get_text("text", sort=True) or "").strip()
    if len(native) >= opts.min_chars_per_page:
        return native, "text"
    if opts.ocr_enabled and _ocr_runtime_ready():
        ocr = _ocr_page(page, fitz, scale=opts.ocr_dpi_scale)
        if len(ocr) >= opts.min_chars_per_page:
            return ocr, "ocr"
    if native:
        return native, "text"
    return "", "image-only"


def extract_pdf(path: Path, *, opts: PdfOptions | None = None) -> tuple[bool, str, str]:
    """Extract page-marked text from a user-named PDF. Returns (ok, name_or_err, text)."""
    options = opts or PdfOptions()
    if not options.enabled:
        return False, ("PDF reading is disabled in config.yaml "
                       "(set pdf_reader_enabled: true)."), ""

    fitz = _import_fitz()
    if fitz is None:
        return False, ("PDF support needs PyMuPDF: pip install pymupdf"), ""

    name = path.name
    try:
        doc = fitz.open(path)
    except Exception as e:
        return False, f"Cannot open {name} as a PDF: {e}", ""

    try:
        if doc.is_encrypted and not doc.authenticate(""):
            return False, (f"{name} is password-protected — I can't read encrypted PDFs. "
                           "Decrypt it first or attach a text export."), ""

        total_pages = doc.page_count
        if total_pages == 0:
            return False, f"{name} is an empty PDF (0 pages).", ""

        cap = max(1, options.max_pages)
        pages_to_read = min(total_pages, cap)
        truncated = total_pages > cap

        text_pages = 0
        ocr_pages = 0
        image_only_pages: list[int] = []
        sections: list[str] = []

        for idx in range(pages_to_read):
            page = doc.load_page(idx)
            body, method = _page_body(page, fitz, opts=options)
            page_no = idx + 1
            if method == "image-only":
                image_only_pages.append(page_no)
                sections.append(f"--- Page {page_no} ---\n"
                                "[NO EXTRACTABLE TEXT — likely image-only or unreadable]")
            else:
                if method == "ocr":
                    ocr_pages += 1
                else:
                    text_pages += 1
                sections.append(f"--- Page {page_no} ---\n{body}")

        meaningful_pages = text_pages + ocr_pages
        ratio = meaningful_pages / pages_to_read if pages_to_read else 0.0
        total_chars = sum(len(s) for s in sections)

        if meaningful_pages == 0 or total_chars < 40:
            ocr_hint = ""
            if options.ocr_enabled and not _ocr_runtime_ready():
                ocr_hint = (" Install Tesseract (brew/apt) plus "
                            "'pip install pytesseract pillow' for scanned PDFs.")
            return False, (f"{name} has no extractable text ({pages_to_read} page(s) checked; "
                           f"likely scanned/image-only).{ocr_hint} I won't guess its contents."), ""

        if ratio < options.min_extractable_ratio and meaningful_pages < 2:
            return False, (f"{name}: only {meaningful_pages} of {pages_to_read} page(s) "
                           "yielded readable text — too little to attach honestly."), ""

        meta = doc.metadata or {}
        title = (meta.get("title") or "").strip()
        author = (meta.get("author") or "").strip()

        header_lines = [
            "[PDF DOCUMENT PROFILE]",
            f"File: {name}",
            f"Pages: {total_pages} total",
            f"Shown: {pages_to_read} page(s)"
            + (" (truncated)" if truncated else ""),
            f"Readable: {text_pages} native text, {ocr_pages} OCR, "
            f"{len(image_only_pages)} image-only",
            f"Characters extracted: {total_chars:,}",
            "Method: PyMuPDF text extraction"
            + (" + Tesseract OCR on sparse pages" if ocr_pages else ""),
        ]
        if title:
            header_lines.append(f"Title: {title}")
        if author:
            header_lines.append(f"Author: {author}")
        header_lines.append(
            "[NOTICE: Reading order may differ from visual layout; figures/diagrams "
            "are not included. Do not claim content from image-only pages.]"
        )
        if truncated:
            header_lines.append(
                f"[PAGE TRUNCATION: showing pages 1–{pages_to_read} of {total_pages}. "
                "Do not claim knowledge of later pages.]"
            )
        if image_only_pages:
            shown = image_only_pages[:20]
            extra = f" (+{len(image_only_pages) - 20} more)" if len(image_only_pages) > 20 else ""
            header_lines.append(
                f"[IMAGE-ONLY PAGES: {', '.join(str(p) for p in shown)}{extra}]"
            )

        body = "\n".join(header_lines) + "\n\n" + "\n\n".join(sections)
        return True, name, body
    finally:
        doc.close()


def load_pdf(path_str: str, max_mb: int | None = None,
             opts: PdfOptions | None = None) -> tuple[bool, str, str]:
    """Validate + extract a user-named PDF. Size check mirrors load_file()."""
    _DEFAULT_MAX_MB = 50
    if not path_str or not path_str.strip():
        return False, "No file path given. Usage: :read <path>", ""
    p = Path(os.path.expanduser(path_str.strip()))
    if not p.exists():
        return False, f"No file at {p} -- check the path.", ""
    if not p.is_file():
        return False, f"{p} is not a regular file.", ""
    try:
        size = p.stat().st_size
    except OSError as e:
        return False, f"Cannot stat {p}: {e}", ""
    limit = int((max_mb if max_mb is not None else _DEFAULT_MAX_MB) * 1024 * 1024)
    if size > limit:
        return False, (f"{p.name} is {size/1024/1024:.1f} MB -- over the {limit//1024//1024} MB "
                       "attach limit. Raise max_attach_mb in config.yaml, or attach an excerpt."), ""
    return extract_pdf(p, opts=opts)
