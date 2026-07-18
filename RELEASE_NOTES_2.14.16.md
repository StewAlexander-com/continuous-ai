<!-- release-title: v2.14.16 — :read DOCX with setup status -->
**TL;DR:** **v2.14.16** lets `:read` attach **Word `.docx`** files (paragraphs + tables via `python-docx`), refuses legacy `.doc` with a convert hint, and shows PDF/DOCX reader readiness in **`:setup`** / **`:status`** with install fix tips when a dep is missing.

## Why this release matters

`.docx` is a widely used document format for reports, policies, forms, notes, and other information users may want Aida to analyze. This release lets Aida read that text directly and reliably, while preserving explicit limits around unsupported content such as images, layout, and legacy `.doc` files.

## What's new / fixed in 2.14.16

- **DOCX attach** — `:read file.docx` extracts paragraphs and tables with a document profile + layout/image omission notice; pages via `:more`.
- **Legacy `.doc` refused** — Word 97–2003 is not supported; message says save/export as `.docx` or PDF.
- **`:setup` / `:status`** — attachment section reports PDF (PyMuPDF) and DOCX (`python-docx`) OK / NEEDS FIX with `pip install …` or `bash setup.sh`.
- **Deps** — `python-docx>=1.1.0` in `requirements.txt`; CI installs it; config keys `docx_reader_enabled`, `docx_max_paragraphs`, `docx_max_tables`.

## Upgrade

```bash
cd continuous-ai
git pull
bash setup.sh       # installs python-docx into the project venv
bash run.sh
```

Or only the new dep:

```bash
~/seedling/.venv/bin/pip install python-docx
```

Fully restart chat, then check `:setup` for `DOCX: OK`.

## Tests

- `test_filereader.py` — **49/49**
- `test_inference_ui.py` — **12/12**

**Full changes:** `v2.14.15..v2.14.16`
