<!-- release-title: v2.14.13 — :read directory browse + follow-up file attach -->
**TL;DR:** **v2.14.13** hardens `:read` for real filesystem paths: soft-corrects missing leading `/`, offers a numbered **directory browse menu** (pick a path or Return to review the listing), pages large listings with `:more`, and **re-attaches an explicitly named child file** after a directory review — without giving the model autonomous filesystem access.

## Why this release matters

Users often typed `Users/...` without a leading slash, or reviewed a directory then asked “summarize `index.html`” — Aida correctly refused invented contents because only the listing was attached. This release makes the runtime recover those mistakes and follow-ups deterministically.

## What's new / fixed in 2.14.13

- **Missing-slash soft fix** — `Users/...` / `home/...` → `/Users/...` / `/home/...` after an exact miss; real relative paths still win.
- **Directory browse menu** — numbered entries (dirs first); Return reviews the listing; `n` cancels; unique basename typing works.
- **Poka-yoke** — out-of-range numbers / ambiguous `y` keep the menu open; vanished or failed opens restore the menu; `:more` / `:read` / exit remain escape hatches.
- **Pageable directory listings** — long listings use the same `:more` staging path as files (no code fence).
- **Directory follow-up** — after an attached directory, “Review the index.html and summarize” re-reads that direct child (no recursion, no symlink inference, ambiguous multi-name stays unresolved).

## Upgrade

```bash
cd continuous-ai
git pull
bash run.sh
```

Fully restart any open chat so `:read` path handling reloads.

## Tests

- `test_filereader.py` — **39/39**
- `test_read_staging.py` — **20/20**

**Full changes:** `v2.14.12..v2.14.13`
