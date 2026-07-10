<!-- release-title: v2.14.4 — full emergent detail in session summary -->
**TL;DR:** **v2.14.4** fixes truncated **Emergent detail** at session end. The full `[EMERGENT]` observation is now captured (up to 500 chars at a word boundary) and printed with terminal-aware wrapping — not chopped at 80 characters mid-word.

## Why this release matters

v2.14.3 fixed a false “truncated reply” log line. Users still saw session summaries like:

```
Emergent detail: The resume reflects your 20+ years experience well, but as a security engineer w
```

Two limits stacked: **200 chars** at capture and **80 chars** at display.

## What's fixed in 2.14.4

- **`extract_emergent_detail()`** — pulls text after `[EMERGENT]` until the next `[Section]` marker (e.g. `[Gödel's …]`).
- **Storage cap raised** — 500 characters, clipped at a word boundary with an honest `…` if needed.
- **`ui.summary_field_lines()`** — wraps long summary fields across aligned continuation lines instead of a hard `[:80]` slice.

`Insight logged` stays a compact one-liner (80 chars). Emergent detection and deliberation logic are unchanged.

## Upgrade

```bash
cd continuous-ai
git pull
bash run.sh
```

## Tests

- `test_session_summary.py` — **8/8** (extraction, ellipsis cap, wrapped display).

**Full changes:** `v2.14.3..v2.14.4`
