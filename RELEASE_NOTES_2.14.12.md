<!-- release-title: v2.14.12 — learning clarity, chat wrap, process honesty -->
**TL;DR:** **v2.14.12** makes learning and chat input easier to understand and use: **`:learning` / `:tune status` / `:tune preview`**, platform-aware line editing (`gnureadline` / `pyreadline3`), terminal-aware reply wrapping, and a **USER-INVOKED PROCESS** guard so methodology metaphors (rubber duck, N-pass review) stay collaborative without softening honesty — while stripping wooden `[EMERGENT]` tags from display.

## Why this release matters

Customers needed clearer Tier 1 vs Tier 2 learning paths, reliable arrow-key editing across macOS/Windows/Linux, readable replies on narrow terminals, and permission to use borrowed thinking structures without Aida refusing good-faith work as “confabulation.” This release keeps the honesty battery intact while improving SNR in chat.

## What's new / fixed in 2.14.12

- **Learning UX** — `:learning`, `:tune status`, `:tune preview`, two-layer tuning gate (safety + data), `tuning_facade` / session-end memory progress.
- **Chat input health** — `:status` + startup check; Darwin `gnureadline`, Windows `pyreadline3`; platform-specific fix commands.
- **Reply wrapping** — word-boundary stream wrap with `Aida:` continuation indent; hint lines break at `|`; code fences preserved.
- **USER-INVOKED PROCESS** — optional brief fit-aside style; passes may stay implicit; complete requested scope (Declaration conclusion clauses); no fake editions/provenance claims; caution bands carve out process metaphors.
- **`[EMERGENT]` display** — audit marker kept in storage / session summary; stripped from streamed chat so replies stay natural.

## Upgrade

```bash
cd continuous-ai
git pull
# macOS (if arrows/delete misbehave):
.venv/bin/python -m pip install 'gnureadline>=8.2.0'
# Windows:
.venv/bin/python -m pip install 'pyreadline3>=3.4.0'
bash run.sh
```

Fully restart any open chat so guards, wrapping, and readline status reload.

## Tests

- `test_process_methodology_guard.py` — **26** checks
- `test_emergent_display.py` — **10** checks
- `test_ui.py`, `test_inputsafe.py`, `test_session_summary.py` — green
- `test_eval_confab.py` — **12** scored cases (honesty battery unchanged)

**Full changes:** `v2.14.11..v2.14.12`
