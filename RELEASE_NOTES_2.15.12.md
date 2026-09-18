<!-- release-title: v2.15.12 — She remembers what would change her mind -->
## In plain language

Aida already remembered what survived disagreement. She did not remember what would make her reverse it, and she did not notice when many individually-reasonable capability steps added up to a state nobody would have accepted all at once.

Noticing an error and changing course are different things. This release keeps a path between the two — without turning every turn into a review, and without claiming she is now always correct.

Durable, model-derived beliefs can optionally keep a **correction envelope**: what the conclusion rests on, what it does not establish, and one specific future observation that should reopen it. Vague boilerplate (`new evidence`, `if I'm wrong`) is dropped rather than stored. Inspect with `:why` / `:belief <id>`.

When you turn on search or widen the allowlist a second time, she can ask one harder question: would this still make sense if all the small steps were viewed as one trajectory? She does not block. `:disable` / `:allow drop` still reverse it.

**Bottom line:** she can show why a durable belief is held, what would reopen it, and when many small capability grants are becoming a trajectory — and ordinary chat is unchanged.

---

**TL;DR:** **v2.15.12** adds optional correction envelopes on deliberated beliefs, `:why` inspection, a correction→state-change audit (not a score), and a gated trajectory check on the second capability-surface expansion. Default path identical when the new fields and gates are not triggered.

## Before (v2.15.11)

A contested synthesis stored the surviving objection. It did not store what future evidence should reopen the belief. There was no inspection command for a single belief's basis and boundary. User corrections still worked; there was no audit of whether a received correction actually changed persistent state. `:enable` / `:allow` each looked locally reasonable; nothing asked about the cumulative surface.

## Now (v2.15.12)

- **Correction envelopes** ([`6557da0`](https://github.com/StewAlexander-com/continuous-ai/commit/6557da0)) — optional `basis` / `boundary` / `disconfirmers` / `affected_by` / `trajectory` / `last_challenged` on `DeliberatedBelief`. Old records load unchanged. Empty reopen is stored as empty; vague lines are discarded.

- **One reopen question at synthesis** (same commit) — folded into the existing synthesis call (`BELIEF:` / `REOPEN:`). Uncontested consensus still exits in one call. No extra round on ordinary conversation.

- **`:why` / `:belief <id>`** — compact view of the conclusion, basis, boundary, surviving objection, and reopen conditions. No chain-of-thought. `:why trajectory` lists gated steps. English `why is the sky blue` stays chat.

- **Correction → state-change audit** — append-only events record `signal_received` vs `state_changed`. Not a corrigibility score. User corrections remain authoritative on the persona layer.

- **Gated trajectory** — first `:enable` / `:allow` add is quiet. A later step in the same capability-surface direction surfaces the cumulative question and does not block. Enable search, enable scan, and allow-folder adds share one direction so renaming a flag or splitting folder adds cannot partition around the gate. Optionality is named (for whom) only on those steps.

## What did not change

Honesty guards, `GUARD_TEXT`, doubt-scope (user facts still bypass deliberation), persona correction matching, memory layering, wall-gate, scan privacy, the reserved `:` channel. No extra latency on ordinary turns. No mandatory thesis/antithesis/synthesis. No vanity metrics.

**Verified:** `test_corrigibility.py` 20/20; `test_deliberation.py` green; `test_replcmds.py` 15/15; `test_correction.py` green; `test_belief_growth.py` green; `test_belief_calculus.py` green; `test_document_osmosis.py` green; `test_inference_ui.py` 15/15; `test_ui.py` 55/55.

**Known limits, named:**

- **Envelopes are optional.** Beliefs formed before this release show `(not recorded)` for basis / reopen. That is honest absence, not fake corrigibility.
- **Trajectory does not auto-block.** It surfaces a question. You still decide.
- **`:why` is an inspection command**, not a new reasoning engine. It shows stored conclusions and boundaries, never hidden chain-of-thought.
- **Restart the session** to load `corrigibility.py` / `schemas.py` / `session.py` / `seedling.py`.

## Upgrade

```bash
cd continuous-ai
git pull
bash run.sh
```

**Restart any running session.** Then `:why` inspects a durable belief if one exists; a second `:enable` / `:allow` may print a trajectory note. No migration. No new config keys. Existing LanceDB records load as before.

**Full changes:** `v2.15.11..v2.15.12`
