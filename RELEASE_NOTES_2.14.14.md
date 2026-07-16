<!-- release-title: v2.14.14 — portable :read directory mtime menu -->
**TL;DR:** **v2.14.14** sorts the `:read` directory browse menu by **modification time** (newest first, oldest at the bottom), shows local dates on each row, and hardens that path for **Windows, macOS, and Linux** — including coarse FAT timestamps, native path separators, and out-of-range/unknown mtimes.

## Why this release matters

After directory browse landed in v2.14.13, newest work was hard to find in alpha-sorted menus. This patch makes recent files surface first and proves the behavior across OSes in CI.

## What's new / fixed in 2.14.14

- **Newest-first menu** — entries sorted by `st_mtime` (content modification), oldest last; equal times break ties with casefolded name.
- **Date column** — each row shows local `YYYY-MM-DD HH:MM`; unknown/unreadable timestamps say `unknown modified` and sort last.
- **Cross-platform hardening** — portable `st_mtime` only (never Windows `ctime` / Unix metadata-change confusion); native trailing separators; Windows timestamp-range fallbacks.
- **CI** — `test_filereader.py` now runs on `ubuntu-latest`, `macos-latest`, and `windows-latest`.

## Upgrade

```bash
cd continuous-ai
git pull
bash run.sh
```

Fully restart any open chat so the directory menu reloads.

## Tests

- `test_filereader.py` — **40/40**
- CI matrix: Linux / macOS / Windows

**Full changes:** `v2.14.13..v2.14.14`
