Aida's **reasoning posture now evolves from experience.** The layer that conditions *every* prompt — her cognitive style and priors — used to sit frozen at neutral defaults no matter how many sessions she logged. v2.5.0 wires it shut: each session's delta is folded into that layer so the frameworks that **survive critique** become the ones she leans on.

The honest distinction that makes this safe: this is **not** model retraining and **not** an LLM rewriting its own personality. It's a small, deterministic arithmetic fold — every change is a printable function of past deltas. You can always ask *why* a value moved and get an exact answer.

### 🧠 What was broken
`cognitive_style` (abstraction level, dominant frameworks, contradiction tolerance, how uncertainty is expressed) and `persistent_priors` (topic salience, trust calibration, self-model confidence) are injected into the system prompt on every turn. But nothing ever *wrote* them from experience — the only code that set those fields lived in a `__main__` smoke test. So Aida recorded growth in her deltas while the layer that actually shapes her stayed generic.

### 🌱 What v2.5.0 does — `consolidation.py`
Each gated delta is folded into that layer:
- **Non-regressive (an absolute):** an exponential moving average. Old signal decays but is **never deleted**; one off-topic session can't wipe accumulated frameworks.
- **Honesty-gated:** quarantined or low-coherence deltas (`<= MIN_INJECT_COHERENCE`) can't reshape cognition. A single mention can't crown a "dominant" framework — it needs to recur.
- **Deterministic:** no model call. Every field move is computed arithmetic over the deltas, so the change is auditable.

```text
end ─► delta extraction ─► MCM.write_delta() ─► L3 consolidation() ─► cognitive_style + priors
```

### 🔁 One-time backfill (on device, revertible)
`backfill_l3.py` consolidates a memory's existing deltas in one pass. It **snapshots full state first** (`state_backup_<ts>.json`) and prints an inspectable before→after report, so you see exactly what moved before it's saved. Revert any time:
```text
./.venv/bin/python backfill_l3.py --revert state_backup_<ts>.json
```

### 🔒 Safety & tests
Scope is strictly L3 — **persona (your truth) and the earned-belief layer are untouched.** **10** new consolidation tests cover non-regression, the honesty gate, bounds, determinism, and the frequency threshold; **17 test suites green**, zero regression in the prior 16.

### Also in this release
- Docs: README architecture table + loop diagram now show the L3 fold; new "Self-shaping cognition (L3)" note under Layered memory.
- Site: new **"Learns your reasoning style"** feature card; architecture flow updated to match.

**Honest scope:** this closes a real defect — the layer was provably frozen, and now it evolves. But whether self-shaping cognition *measurably sharpens Aida's answers* is a **hypothesis pending an A/B eval**, not a proven result. It is shipped as a corrected mechanism, not a validated quality gain. The eval is the honest next step.

**Full changes since 2.4.0:** `v2.4.0..v2.5.0`
