<!-- release-title: v2.14.8 — silent system clock (no stamp every reply) -->
**TL;DR:** **v2.14.8** keeps the visceral host clock from v2.14.7, but stops Aida from **pasting the date/time into every reply**. The clock is silent orientation — recite it only when asked, or when the question truly has a time dimension.

## Why this release matters

After v2.14.7, she correctly knew “Wednesday, July 15, 2026…” — then led with it on thanks, small talk, and unrelated turns. Same class of bug as announcing the internal working register: awareness leaked into narration.

## What's fixed in 2.14.8

- **SYSTEM CLOCK** block marked silent — do not recite unless asked / time-relevant.
- **TEMPORAL INTEGRITY** — silent orientation, not a stamp to paste every turn.
- **Per-turn prompt** — “otherwise do not mention the date or time at all.”

Cutoff ≠ calendar, portable Win/Mac/Linux formatting, and model-id awareness are unchanged.

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

**Full changes:** `v2.14.7..v2.14.8`
