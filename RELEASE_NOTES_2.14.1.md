<!-- release-title: v2.14.1 — :read path picks + confabulation fix -->
**TL;DR:** **v2.14.1** fixes a honesty bug where `You: :read <path>` could reach the model and produce **invented file contents**, and adds an interactive **“Did you mean …?”** pick list when a `:read` path fails — real files only, confirmed with `y`, a number, or `n` (Return dismisses).

## Why this release matters

v2.14.0 added PDFs and globs. **v2.14.1 makes mistyped paths safe and usable** — no more guessing file contents, no more retyping long paths when you're one character off.

- **Confabulation fix** — echoed `You:` prefixes are stripped; bare `:read` lines never fall through to chat as a normal turn.
- **Interactive picks** — typo `:read ~/seedling/voce.py` → numbered list of real neighbors; reply `1`, `y` (single match), or `n`.
- **Tighter suggestions** — stem-based fuzzy match (e.g. `voce` → `voice.py`, not unrelated `.py` files).

## What's new in 2.14.1

### Interactive `:read` disambiguation

When a path does not exist (and glob expansion does not apply), the runtime searches **only the directory you named** and offers up to 12 real files:

```
:read ~/project/voce.py
  Did you mean one of these?
    1  .../voice.py
    2  .../test_voice.py
    3  .../voicelayer.py
  Reply with a number, or  n  to cancel.
  Press Return on an empty line to dismiss without attaching.
```

- **Never auto-reads** — you must confirm.
- **Multi-match:** `y` alone is rejected (use a number).
- **Single match:** `y` or `1` works.
- New config keys: `read_suggest_enabled`, `read_suggest_max`, `read_suggest_min_score`.

### Honesty hardening (bug fix)

- **`You: :read foo.py`** (pasted prompt echo) now dispatches as `:read` instead of becoming a chat turn.
- Session guard strengthened: do not output file contents unless the turn includes `[USER-ATTACHED FILE: ...]`.
- Safety net in the REPL loop: `:read` lines cannot be sent to the model as plain chat.

## Upgrade

```bash
cd continuous-ai
git pull
# no new pip deps — same as v2.14.0 (pymupdf for PDFs)
bash run.sh
```

Try a deliberate typo:

```
:read ~/seedling/voce.py
```

Then reply `1` or `3`.

## Tests

- Filereader suite **30/30** (pick menu, stem ranking, parse `y`/`1`/`n`).
- Inputsafe **45/45** (`You:` prefix normalization).

**Full changes:** `v2.14.0..v2.14.1`
