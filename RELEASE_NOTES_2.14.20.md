<!-- release-title: v2.14.20 — Harden local-only guards without patch-by-prompt overfitting -->
## In plain language

This patch makes Aida’s **offline promise enforceable on the default path**, keeps her **caution from mistaking grader glitches for bad answers**, and stops stuffing every past eval failure into the giant honesty prompt as another paragraph.

**Bottom line:** same honesty battery — cleaner guards, real local-only enforcement, quieter tone under infra noise.

---

**TL;DR:** **v2.14.20** splits case-specific guard regressions into a versioned patches block, validates `OLLAMA_HOST` for the default Ollama backend, excludes critic parse failures from the caution buffer, makes MCM signal handlers opt-in for library embeds, and crash-safes context-state upserts.

## Why this release matters

v2.14.19 fixed VRAM thrash. A review of the honesty stack found a different class of debt: eval-coupled phrases hard-coded into `_GUARD_TEXT`, a “cloud URLs are blocked” claim that only covered `openai_compat`, and caution that could drift RESTRAINED from critic JSON parse noise rather than model behavior. Patch-by-prompt does not scale on a 3B context window — this release separates *principles* from *case patches* so the core stays auditable.

## What's new / fixed in 2.14.20

- **Versioned regression patches (`guards.py`)** — Declaration-clause scripts, 5-pass style recipes, and exact forbid markers like `[RETRIEVAL COMPLETE]` live in a versioned patches block; core guard text stays principle-level. Assembled `GUARD_TEXT` is unchanged for the confab battery.
- **`OLLAMA_HOST` local-only** — default `OllamaBackend` rejects non-loopback hosts at construction (closes the gap where only `openai_compat` was validated).
- **Caution ignores infrastructure noise** — critic parse / unavailable / error placeholders (neutral 0.5) no longer feed the caution buffer, wallgate lag, or session coherence average.
- **Crash-safe context upsert** — `save_context_state` adds first, then prunes older rows; delete predicates require a UUID session id.
- **Library-safe MCM signals** — `SIGINT`/`SIGTERM` handlers are opt-in (`install_signal_handlers=True` only from CLI chat entrypoints).
- **Objection-strength polarity** — negated strong markers (`not false`) no longer inflate agreement pressure; confusion-matrix unit coverage added.
- **Scorer docs** — `score_response` docstring matches the case-insensitive forbid behavior the code already used.

## What did not change

Confabulation battery surface, downward-only caution law, osmotic learning, and dual-model residency from v2.14.19 are unchanged. No model swap required.

## Upgrade

```bash
cd continuous-ai
git pull
bash run.sh
```

If you previously pointed `OLLAMA_HOST` at a remote machine, this release will refuse to start the default backend until you use a loopback host (or switch to an explicitly local `openai_compat` URL).

## Tests

- `test_guards_regression_patches.py` — core vs patches split + version pin
- `test_llm_backend.py` — `OLLAMA_HOST` rejection / loopback accept
- `test_caution_wiring.py` — parse-error evals excluded from restraint
- `test_storage_upsert.py` — UUID-safe predicate + add-then-prune
- `test_mcm_signals.py` — default leaves host handlers alone
- `test_deliberation.py` — objection-strength confusion matrix
- Existing guard / confab scorer suites — green

**Full changes:** `v2.14.19..v2.14.20`
