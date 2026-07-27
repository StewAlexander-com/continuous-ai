<!-- release-title: v2.14.18 — Osmotic learning + foreground-priority responsiveness -->
**TL;DR:** **v2.14.18** teaches Aida by *osmosis* — highest-SNR, ease-of-use signal only, never deleting what she already knows — and keeps the chat responsive while that learning runs in the background by giving the user’s turn exclusive claim on the local GPU.

## Why this release matters

Learning used to compete with conversation for the same scarce resource (one local GPU). Background deliberation and critic grades already ran on other threads, but they still sat in front of the next reply in the inference queue — so “background” was true for threads and false for hardware. This release closes that gap, and adds a measured, non-regressive osmotic learning loop so exposure to context and documents can reinforce what is useful without flooding memory or inventing confidence.

## What's new in 2.14.18

### Osmotic learning (non-regressive)

- **Usage utility (SNR = ease of use)** — each deliberated belief now tracks inject / used / correction-adjacent counts and a `usage_utility()` score. Measurement only on the reply path; no archive/evict/reorder from these counters alone.
- **Osmotic reinforce / decay** — at session end, useful beliefs get a capped salience nudge; unused ones decay gently. Worst-case decay ends in *revivable* quarantine, never deletion.
- **Promotion budget + eviction tiebreaker** — per-session cap on new osmotic promotions (`osmosis_promotion_budget`); when memory is full, low usage utility loses the tie.
- **Reflection sleep pass (`:reflect`)** — offline contradiction sweep, archive parole (only with external recurrence evidence), and delta mining across threads. Opt-in at session end via `reflection_on_session_end`. Hard deliberation budget; safety snapshot before mutation.
- **Document osmosis** — attached documents can seed contested, provenance-tagged candidates through the existing deliberation path. Retract a whole source with `:forget-doc <hash|prefix>` (archive, not delete).

### Foreground-priority responsiveness

- **Foreground gate** (`scheduler.py`) — `chat()` marks the GPU busy for the whole turn; critic grades and every live-deliberation *round* wait for clearance first (starvation escape: `background_max_deferral_s`).
- **Bounded background calls** — token-capped (`background_num_predict: 512`, sized by measurement on qwen3), with `think=False` + scrubbing so truncated chain-of-thought never becomes a belief.
- **Timing instrumentation** — every model call logs `[timing] role=chat|critic|delib_live wait=… call=…` plus a `model_call` event, so slow turns are diagnosable.
- **Pinned small critic** — default local critic is `gemma3:4b` (chosen by live grading quality, not size alone). `:model` / `--model` no longer silently re-inflate a deliberately pinned critic.
- **Hardening** — drain mode at `end()` skips the gate so shutdown stays bounded; race-free gate singleton; thread-safe event log; kill switch (`background_gate_enabled: false` / `background_num_predict: 0`) restores the pre-feature call shape.

## New commands / knobs

| Command / knob | Purpose |
|---|---|
| `:reflect` | Run one sleep pass now |
| `:forget-doc <hash\|prefix>` | Quarantine all beliefs from that document source |
| `osmosis_*`, `reflection_*`, `document_osmosis_enabled` | Learning tunables in `config.yaml` |
| `background_gate_enabled`, `background_max_deferral_s`, `background_num_predict` | Responsiveness tunables |

## What did not change

Honesty gates (`MIN_INJECT_COHERENCE`, quarantine, deliberation), L2 persona vs L2b belief separation, EMA L3 consolidation, and “archive, don’t delete” remain intact. Osmosis never auto-promotes persona facts. Foreground replies are never gated by background work.

## Upgrade

```bash
cd continuous-ai
git pull
bash run.sh
```

Fully restart any open chat so the new scheduler, critic model, and osmosis knobs load. First run after upgrade may pull/load `gemma3:4b` if it is not already local. Optional checks: `:reflect`, watch `[timing]` lines in `logs/seedling.log`.

## Tests

- `test_osmosis_utility.py` — **7/7**
- `test_osmosis_reinforce.py` — **6/6**
- `test_osmosis_economics.py` — **5/5**
- `test_reflection.py` — **10/10**
- `test_document_osmosis.py` — **7/7**
- `test_background_priority.py` — **9/9**
- `test_gate_hardening.py` — **13/13**
- Full `test_*.py` suite — green

**Full changes:** `v2.14.17..v2.14.18`
