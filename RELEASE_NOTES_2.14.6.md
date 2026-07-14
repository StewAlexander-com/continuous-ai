<!-- release-title: v2.14.6 — :read honesty: reason beyond docs without polluting memory -->
**TL;DR:** **v2.14.6** tightens `:read` honesty without silencing Aida. She can **hypothesize beyond an attachment** (labeled), while gated **Chain-of-Verification** catches invent-risk only at DECLINE_FIRST caution. Also fixes a bug where file prose (`Always…` / `Never…`) false-promoted the attach header into persona memory.

## Why this release matters

After citation framing landed, Aida sometimes refused useful beyond-doc analysis (“the document provides no pathways”) even when asked for ideas. Separately, attaching a PDF could save noise like `[USER-ATTACHED FILE: …]` as a durable constraint — because document imperatives matched persona directive patterns.

## What's new / fixed in 2.14.6

- **Beyond-doc reasoning** — `:read` asks and attach framing allow labeled hypotheses; forbid inventing unread pages or putting words in the document’s mouth.
- **Citation hygiene** — don’t quote paraphrases as document text; don’t attribute methods to a bare author/year citation unless the shown text states them; hedge speculative multipliers.
- **Thin CoVe** (`verify.py`) — optional second-pass rewrite only when caution `applied_d ≥ 0.68` (DECLINE_FIRST). Draft is buffered (not streamed) on those rare turns. Fail-safe keeps the original. Config: `chain_of_verification_enabled`, `cov_min_applied_d`.
- **Attach persona pollution** — directive scan uses the ask tail only when `[USER-ATTACHED FILE:` is present; promote/restore skip/prune attach-header “facts”.

## Upgrade

```bash
cd continuous-ai
git pull
bash run.sh
```

Restart any open chat so the new prompts and CoVe wiring load. If an older session already saved an attach-header constraint, the next restore prunes it automatically (or use `bash run.sh forget`).

## Tests

- `test_verify.py`, `test_cove_wiring.py` — CoVe gate / fail-safe / streaming buffer
- `test_correction.py` — attach turns do not promote file headers
- `test_read_staging.py`, `test_filereader.py` — citation + beyond-doc ask framing
- Full `test_*.py` suite green

**Full changes:** `v2.14.5..v2.14.6`
