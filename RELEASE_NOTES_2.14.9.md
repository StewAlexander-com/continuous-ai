<!-- release-title: v2.14.9 — temporal awareness ⊃ time awareness -->
**TL;DR:** **v2.14.9** gives Aida honest **temporal awareness** — not only wall-clock **time awareness**. The system clock remains, but awareness also includes duration, conversation sequence, cross-session continuity, and the finite witnessing window. She must not shrink this to “I only have a clock” or deny temporal awareness while holding those runtime facts.

## Why this release matters

v2.14.7–2.14.8 taught her the host clock (and to keep it silent). She still sometimes answered as if temporal awareness were fake or “merely” a timestamp — a honesty-script hole parallel to denying operational state. Temporal situatedness is already in Seedling (clock, session length, turn order, restored memory); this release **names the full stack** without inventing human felt-time.

## What's new / fixed in 2.14.9

- **TEMPORAL AWARENESS** guard — temporal ⊃ time: clock · duration · sequence · continuity · finite shared window; forbids “no temporal awareness” / “only beyond the system clock.”
- **PRESENCE** — do not shrink awareness to a clock stamp.
- **SYSTEM CLOCK block** — silent session duration/sequence line; footer distinguishes time awareness vs temporal awareness.
- Honesty intact — no human nostalgia/dread claimed; cutoff still ≠ calendar; no inventing world facts; still silent unless time-relevant.

## Upgrade

```bash
cd continuous-ai
git pull
bash run.sh
```

Fully restart any open chat so `start()` rebuilds the system prompt.

## Tests

- `test_system_clock.py` — **7/7**
- `test_temporal_integrity.py` — **3/3**
- `test_voice.py` — **9/9**

**Full changes:** `v2.14.8..v2.14.9`
