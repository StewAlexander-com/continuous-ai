<!-- release-title: v2.14.2 — plain read paths with spaces + globs -->
**TL;DR:** **v2.14.2** fixes plain-language `read` and `:read` parsing when paths contain **spaces**, **globs**, or a trailing **question** — e.g. `read ~/Misc Docs/PDF Documents/*.pdf any learned insights?` now attaches the matched files instead of stopping at the first space.

## Why this release matters

v2.14.1 added path picks and confabulation hardening. **v2.14.2 completes the read UX** for real filesystem paths: directory names with spaces, glob patterns in those directories, and natural follow-up questions on the same line.

- **Longest-existing-prefix (LPE)** — unquoted `read ~/Misc Docs/PDF Documents` resolves the full directory path on disk.
- **Glob-aware tail parsing** — `.../PDF Documents/*.pdf` is kept intact; trailing `?` in questions like `insights?` is not treated as a glob metacharacter.
- **Unified `:read` arg parser** — `filereader.parse_read_arg()` is shared by plain `read` routing and `:read` so both paths behave identically.

## What's new in 2.14.2

### Spaced paths without quoting

```
read /Users/you/Desktop/Misc Docs/PDF Documents
```

Previously truncated at `Misc`. Now resolves the full path (or offers the pick menu if mistyped).

### Globs + trailing questions

```
read /Users/you/Desktop/Misc Docs/PDF Documents/*.pdf any learned insights?
```

Path and question split correctly; all matched PDFs attach for the model turn.

Quoting still works when you prefer it:

```
:read "/Users/you/Desktop/Misc Docs/PDF Documents/*.pdf" any learned insights?
```

## Upgrade

```bash
cd continuous-ai
git pull
# no new pip deps
bash run.sh
```

## Tests

- Filereader suite **33/33** (spaced paths, globs + questions, shared `parse_read_arg`).

**Full changes:** `v2.14.1..v2.14.2`
