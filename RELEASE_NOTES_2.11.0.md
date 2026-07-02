**TL;DR:** Aida gains a **graded sense of caution** and a **much sharper collaborative instinct**. A new forward-acting *caution-disposition controller* turns lagged self-critique into a downward-only restraint she applies *before* her next reply — no gauge writes, no reply-path model calls, fully deterministic and auditable. The collaborative wall is now **on by default but high-fidelity**: a cheap, model-free pre-gate decides whether a turn is genuinely difficult enough to be worth the expensive deliberation, so she only turns to you on the hard ones. Plus quieter, cleaner local voice. With every new subsystem **off/neutral by config**, prior behavior is preserved.

### ✨ Added
- **Caution-disposition controller (`caution.py`).** A forward-acting, **downward-only** restraint layer. It reads only *lagged* CRITIC-derived signals (coherence integral, trend, correction recency) and maps them through a fuzzy control law to a graded disposition, quantized into assertion-restraint bands (`OFF → GUARDED → RESTRAINED → DECLINE_FIRST`) injected into the system prompt **before** the next reply.
  - **No gauge writes, no reply-path model calls.** It never mutates `self_model_confidence`/priors and never syncs the critic on the reply path (zero added latency).
  - **Downward-only + crisp floors.** Fuzzy shapes the middle; crisp floors (recent-correction, cross-session prior) can only *raise* caution, never lower it below the raw control law.
  - **Deterministic & auditable.** Every evaluation returns a full `CautionReport` (rules fired, floors, applied disposition) that prints for audit.
- **High-fidelity wall pre-gate (`wallgate.py`).** A cheap, **model-free**, deterministic gate that decides whether a turn is *difficult enough* to spend the wall's expensive synchronous deliberation on. Difficulty is a trust-weighted **noisy-OR** of: the caution disposition, recent CRITIC coherence, decision/trade-off markers in your ask, uncertainty markers in the reply, and correction recency. Calibrated signals can clear the bar alone; gameable heuristics must converge. A **cooldown** and **per-session cap** keep it rare even on a long, hard thread.

### 🔧 Changed
- **The collaborative wall is now high-fidelity, not always-on-cost.** Previously, when enabled, it paid ~4–7 model calls on *every* substantive turn just to discover it was not a wall. Now the pre-gate fires the deliberation **only on genuinely hard turns**; the existing conservative fuzzy `at_wall()` decision (whether to actually surface it) is **unchanged**. Enabled by default, and rare by construction.
- **Default model → `qwen3:30b-a3b`** (MoE; strong reasoning that fits an M1 Max). Set `model_name`/`base_model` back to `qwen2.5:14b` in `config.yaml` if you prefer the previous default.

### 🔒 Honesty / safety (the point, as always)
- **No new honesty surface.** The caution controller only ever *raises restraint*; the confabulation battery is untouched and still **0%** (verified with the `DECLINE-FIRST` band injected via `eval_confabulation.py --caution-on`).
- **The wall's honesty contract is intact.** The pre-gate sits *before* deliberation and changes nothing about how a wall is surfaced: still a question about *her* reasoning (never a smuggled fact), still no auto-promote, overruled dissent still kept.
- **Non-regressive.** With `caution_controller_enabled: false` and `collaborative_wall_enabled: false`, behavior matches the prior release. Every new subsystem degrades gracefully (a missing signal contributes nothing rather than misfiring).

### 🩹 Fixed (local voice)
- **Quieted the cosmetic `phonemizer: words count mismatch` warning** from espeak inside Kokoro — it bled mid-conversation; audio always played. Now suppressed like the `httpx` loggers.
- **TTS text normalization (audio only).** Em/en-dashes and smart quotes are mapped to plain ASCII before synthesis, so speech sounds cleaner and stops tripping espeak's word-count check. Applied *after* the voice floor decides what may be spoken — the **spoken ⊆ printed** invariant is untouched; the printed reply never changes.

### ⚙️ Config
```yaml
caution_controller_enabled: true    # forward-acting, downward-only restraint
caution_integral_half_life: 3.0
caution_wall_session_cap: 0.65

collaborative_wall_enabled: true     # now high-fidelity (pre-gated)
wall_gate_cutoff: 0.50               # difficulty needed to SPEND a deliberation
wall_gate_cooldown_turns: 3          # min turns between collaborative asks
wall_gate_max_per_session: 3         # hard cap on asks per session
```

### ✅ Tests
- New: `test_wallgate.py` (29, incl. calibrated-signal triggers, single-heuristic suppression, monotonicity, determinism, tunable cutoff, audit fields) and the `test_caution_*` suite (downward-only, no-gauge-write, deterministic, fail-safe, orthogonality, wiring, induced-scorer).
- Full offline suite **31/31 green**; `compileall` + `schemas.py` + `eval.py` clean. Confab battery **0%** with the caution band injected.

**Full changes:** `v2.10.0..v2.11.0`
