<!-- release-title: v2.14.7 — visceral host system clock (Win/Mac/Linux) -->
**TL;DR:** **v2.14.7** gives Aida a lived **host OS wall clock** — weekday, full calendar date with year, local time, timezone offset, and ISO stamp — injected at session start and every turn. She orients time-dimension questions from that present instead of treating her knowledge cutoff as “today.”

## Why this release matters

Without a year (and with only soft time-of-day cues), small models conflated training end-date with the calendar: claiming past months “had not yet occurred,” or replacing answers with a knowledge-cutoff monologue. Aida needs the machine’s real clock as a shared present with the user — portable across macOS, Linux, and Windows.

## What's new / fixed in 2.14.7

- **`voice.system_clock_block`** — single portable formatter (stdlib only; no NTP, no shell `date`). Spelled weekday + date + part-of-day + zone/offset + ISO + inhabit line; optional running model id.
- **Session start + per-turn** — both inject the same block (local wall time via `datetime.now().astimezone()`).
- **`TEMPORAL INTEGRITY`** guard — inhabit the system clock; cutoff is coverage of the world, not the calendar; no cutoff monologue as a wholesale refuse; treat the running model id as known.
- **Clock ≠ cutoff** — prefer answering time-filtered questions from recall with hedges over inventing names/scores or rewinding the year.

## Upgrade

```bash
cd continuous-ai
git pull
bash run.sh
```

Fully restart any open chat so `start()` rebuilds the system prompt with the new clock block.

## Tests

- `test_system_clock.py` — **7/7** (portable offset, visceral block, shared formatter, prompt lead)
- `test_temporal_integrity.py` — **3/3**
- `test_voice.py` — **9/9**

**Full changes:** `v2.14.6..v2.14.7`
