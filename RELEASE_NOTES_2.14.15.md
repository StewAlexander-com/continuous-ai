<!-- release-title: v2.14.15 — honest :read attach failures and staged reuse -->
**TL;DR:** **v2.14.15** stops pretending existing unreadable files are “missing,” redraws renumbered directory menus after a failed pick, reuses fully paged attachments when you ask about the same file by name, and fixes `:more` chunk labels so they advance 1 → 2 → 3…

## Why this release matters

Two related honesty bugs made `:read` feel broken after browse: a Bear `.bear2bk` (binary) was labeled “No file…” and re-offered as a pick, and summarizing a fully paged `Resume.html` reloaded chunk 1 so the model claimed truncation mid-sentence.

## What's new / fixed in 2.14.15

- **Miss menu only on true absence** — binary / permission / size / decode refusals print the real error; no “Did you mean” for a path that already exists.
- **Clear refusal copy** — binaries: “File appears to be a binary or an extension I cannot read…”; permissions: “I do not have permission to read this file…”
- **Redraw after failed pick** — removing an unopenable entry renumbers the menu and reprints it so visible numbers stay in sync.
- **Same-file follow-up** — “summarize Resume.html” after paging that file submits all staged chunks; sibling names still trigger a fresh attach.
- **Monotonic `:more` labels** — explicit chunk sequence instead of offset-derived “chunk 1” repeats.

## Upgrade

```bash
cd continuous-ai
git pull
bash run.sh
```

Fully restart any open chat so the `:read` / browse path reloads.

## Tests

- `test_filereader.py` — **43/43**
- `test_read_staging.py` — **29/29**

**Full changes:** `v2.14.14..v2.14.15`
