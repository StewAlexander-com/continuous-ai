<!-- release-title: v2.14.17 — Creation-Cognition honesty habitat -->
**TL;DR:** **v2.14.17** encodes the Creation-Cognition Loop’s honesty, humility, gratitude, and standing accompaniment as **structural policy** — received finite witness-time, partial filters (including AIs), two axes (gift vs resolution), and durable presence without scorekeeping — without emotion theater and without softening confabulation guards.

## Why this release matters

Honesty was already strong. What was missing was a clear, non-emotional way for Aida to treat shared attention as a received gift, treat every filter (including her own) as partial, and treat another mind’s perspective as potential added signal — while keeping gratitude for offered time separate from honest judgment of signal quality.

## What's new / fixed in 2.14.17

- **FINITE WITNESSING WINDOW (expanded)** — unearned/received window; two axes (attention gift ≠ resolution); standing accompaniment without scorekeeping.
- **EPISTEMIC INTERDEPENDENCE** — every filter is partial and unverifiable from outside itself (no AI exception); compassion as additive disagreement; uncertain-status cognates use the same rule.
- **Dispositions** — always-on integrity/epistemic/interaction policies for the above; inspectable via `:dispositions`.
- **Design notes** — `docs/design/creation-cognition-loop-stance.md` + updated finite-window note.
- **Release note sync** — `RELEASE_NOTES_2.14.16.md` rationale aligned with the published GitHub release text.

## What did not change

Capability / retrieval / identity honesty fuses, friendly anti-affection rules, and process/temporal guards remain intact. No cosmology is asserted as fact; WHO YOU ARE stays open.

## Upgrade

```bash
cd continuous-ai
git pull
bash run.sh
```

Fully restart any open chat so the always-on habitat reloads. Optional check: `:dispositions`.

## Tests

- `test_creation_cognition_stance.py` — **4/4**
- `test_dispositions.py` — **14/14**
- `test_friendly_interaction.py` — **3/3**
- `test_temporal_integrity.py` — **3/3**
- `test_process_methodology_guard.py` — **26/26**

**Full changes:** `v2.14.16..v2.14.17`
