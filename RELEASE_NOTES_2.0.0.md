# Continuous-AI v2.0.0 — The Deliberation Release

**The headline:** v1.0 gave a local LLM a *memory*. v2.0 gives it a way to **form, defend, and curate its own beliefs** — earned through friction, honest about their limits, and never at the expense of the conversation or of the truths you state.

All of it runs locally on top of Ollama, stays off the reply path so chat stays responsive, and is covered by **9 test suites plus a 17-check end-to-end smoke test** (`bash run.sh smoke`) that runs against the live model in an isolated database.

---

## Major new capabilities (the delta from 1.0)

### 🧠 Deliberated beliefs (thesis → antithesis → synthesis)
A model-derived insight no longer enters durable memory just because one pass produced it. Each is challenged by an antithesis voice that hunts the single strongest objection; a synthesis must *account for* that objection rather than bury it. **Consensus is treated as low-information** (an explicit anti-echo-chamber bias); a belief that survives a real objection is what earns its place — and the dissent is kept, not flattened.

### ⚖️ Adaptive, two-speed, never-stalemate deliberation
Depth scales with disagreement, not the clock: a weak objection earns one round, a strong one earns more, **hard-capped (`MAX_ROUNDS=3`)** so the model always answers. **Live (per-turn)** deliberation runs in the *background, off the reply path*; the **end-of-session** pass can think a little harder.

### 🌱 Beliefs that grow across threads
Surviving syntheses are promoted into an injected, accumulating **earned-belief layer** that future sessions see and can reinforce or revise — the context map now *grows* deliberated understanding over time, while an append-only ledger remains the full audit trail.

### 🔁 Autonomous belief calculus — conflict resolution + SNR self-pruning (non-regressive)
Beliefs self-curate via a **deterministic signal score** (grows with re-earning and information content; decays with age and lost conflicts). Contradictions are detected (negation-aware, so a conflict is never mistaken for agreement) and **resolved by the same deliberation**. Low-signal and cap-evicted beliefs are **quarantined, not deleted** — retained, auditable, and **revived if re-earned**. *Nothing the model would still hold is ever silently destroyed.*

### 🎯 Doubt-scope guard — doubt must be *real*
Deliberation may challenge the model's **own reasoning**, but never a fact you stated about yourself (name, location, job, preferences, behavior directives). User-anchored truth bypasses the doubt machine entirely. Enforced deterministically on both the live and end-of-session paths — no more "uncertain whether the user lives in Mebane."

### ⚡ Responsiveness — it's a conversation first
- **Replies stream token-by-token** (time-to-first-token, not time-to-full-response).
- **The Critic grades in the background**, off the reply path (it was a second model call doubling latency).
- **Bounded history window** keeps per-turn latency flat as a chat grows; the full transcript is still persisted.
- Model kept warm via `keep_alive`.

### 🔎 Honest transparency — you can tell what it's doing
- A subtle blinking **"Aida is working…"** indicator while waiting for the first token (erases itself the instant streaming starts; never overlaps the answer).
- A per-turn **mechanism trace** (`grading reply · deliberating in background`) — reports work *started*, never claims an outcome it doesn't have yet.
- An **end-of-session summary** (deliberations · contested · pruned · active/archived beliefs).
- `status` now separates **your authoritative facts** from the model's **earned beliefs** (with signal scores) — explicitly labeled, no claims of mind.

### 📊 Measured, not claimed — eval harness
A reproducible confabulation/persistence eval with a tested scorer and multi-run averaging. **Result: guards cut a 3B model from ~20% to 0% confabulation over 5 runs** (ablation proves the guards, not just scale, do the work). Honest scope kept throughout: a smoke/adversarial battery, not a published benchmark.

### 🩺 Confabulation guards (earlier in this line, consolidated here)
- **Capability-boundary guard** — the model declines to "read" URLs/files (it's offline) and asks you to paste text instead of inventing contents.
- **Identity-disambiguation guard** + meta-directive promotion filter.
- **Live, deterministic memory correction** — fix a stored fact in plain language; the runtime locates and replaces it by *your* words. The model never decides what to delete.
- **Named-work accuracy guard** — hedge exact titles rather than fabricate them.

### 🔧 Quality-of-life
- **`qwen2.5:14b` is now the default model** (stronger guard adherence than a 3B); clean model switching via `config.yaml` (single source of truth) + `--model` override.
- **Quiet, conversational logs** — the chat view stays clean; the full trail lives in `logs/seedling.log` (`tail -f` to watch; `LOG_CONSOLE=1` or `log_level: DEBUG` for verbose console).
- **`bash run.sh smoke`** — one-command end-to-end verification.
- Easy install kept current: `setup.sh` installs from `requirements.txt` and pulls the model from `config.yaml`.

---

## Honest scope (unchanged philosophy)
This is **contradiction-driven belief revision, not "self-awareness,"** and there is no literal fractal geometry — those are inspiration, not mechanism. Deliberation runs only on the model's own insights; user-stated facts and corrections stay verbatim. The local self-critique score is a weak, same-model signal by design. Results depend on your hardware and model.

## Compatibility
Backward compatible: new belief fields default, so existing `.seedling_db` state from 1.0 loads cleanly. macOS on Apple Silicon, Python 3.11–3.13, Ollama.

## Verify it yourself
```bash
bash setup.sh        # venv + deps + the right model
bash run.sh          # chat
bash run.sh smoke    # 17-check end-to-end smoke test (live model, isolated DB)
```

**Full commit log:** `v1.0.0..v2.0.0` (35 commits).
