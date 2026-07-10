<!-- release-title: v2.14.5 — wrap session-end insight in summary -->
**TL;DR:** **v2.14.5** fixes truncated **Insight logged** at session end. Long insights now wrap across terminal-aware continuation lines — the same treatment v2.14.4 gave emergent detail — instead of a hard `[:80]` slice mid-word.

## Why this release matters

v2.14.4 wrapped **Emergent detail** but **Insight logged** still chopped at 80 characters:

```
  Insight logged : User's "thanks" after concise response confirms natural closing point **only whe
  Coherence      : 1.00
  Emergent       : False
```

The stored insight was complete; only the display was misleading.

## What's fixed in 2.14.5

- **`ui.print_session_end_summary()`** — one shared session-end block for `seedling.py chat`, the `session.py` dev harness, and consistent emergent display.
- **Insight logged** — full text, word-wrapped via `summary_field_lines()` (no `[:80]`).
- **Emergent** — when flagged, detail prints under the **Emergent** label (wrapped); `False` stays a compact one-liner.
- **`seedling status`** — `last insight` and `last emergent` use the same wrapping.

Emergent detection, deliberation logic, and storage caps are unchanged.

## Upgrade

```bash
cd continuous-ai
git pull
bash run.sh
```

## Tests

- `test_session_summary.py` — **17/17** (extraction, ellipsis cap, insight wrap, unified end block).

**Full changes:** `v2.14.4..v2.14.5`
