<p align="center">
  <h1 align="center">Continuous-AI</h1>
</p>

<p align="center">
  <strong>Give your local LLM a memory. A fully offline AI runtime on Ollama that remembers across sessions, critiques its own answers, and can self-tune — on Apple Silicon, no cloud.</strong>
</p>

<p align="center">
  <a href="https://github.com/StewAlexander-com/continuous-ai/actions/workflows/ci.yml"><img src="https://github.com/StewAlexander-com/continuous-ai/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://stewalexander-com.github.io/continuous-ai/"><img src="https://img.shields.io/badge/live%20site-stewalexander--com.github.io-F7923B" alt="Live site"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11%E2%80%933.13-blue" alt="Python 3.11–3.13"></a>
  <img src="https://img.shields.io/badge/platform-macOS%20Apple%20Silicon-lightgrey" alt="Platform: macOS Apple Silicon">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green" alt="License: MIT"></a>
  <img src="https://img.shields.io/badge/cloud-not%20required-success" alt="Cloud: not required">
</p>

<p align="center">
  <a href="https://stewalexander-com.github.io/continuous-ai/" title="Open the Continuous-AI site">
    <img src="docs/assets/readme-hero.png" width="820" alt="Continuous-AI — give your local LLM a memory">
  </a>
  <br>
  <sub><a href="https://stewalexander-com.github.io/continuous-ai/">stewalexander-com.github.io/continuous-ai →</a></sub>
</p>

<p align="center">
  <a href="https://stewalexander-com.github.io/continuous-ai/" title="Open the Continuous-AI site">
    <img src="docs/assets/demo.gif" width="760" alt="Continuous-AI chat session: launch, chat, self-critique, delta stored, memory restored">
  </a>
  <br>
  <sub>A continuity-enabled session: chat → self-critique → delta written → memory restored.</sub>
  <br>
  <sub><i>Faithful AI-rendered recreation of a real session — not a live screen capture. Every step and value mirrors actual runtime behavior.</i></sub>
</p>

<p align="center">
  <a href="#quickstart">Quickstart</a>
  ·
  <a href="#what-it-does">What it does</a>
  ·
  <a href="#why-this-matters--and-who-its-for">Why it matters</a>
  ·
  <a href="#architecture">Architecture</a>
  ·
  <a href="#commands">Commands</a>
  ·
  <a href="#self-tuning-rdst">Self-tuning</a>
</p>

---

Most local LLM setups are amnesiacs: every chat starts from zero. **Continuous-AI** adds a persistent, versioned, machine-writable memory layer on top of [Ollama](https://ollama.com) so the model carries its *reasoning state* — not just a chat log — from one session to the next. A second model pass scores every answer for coherence and drift, and that signal can optionally drive local LoRA fine-tuning.

**Why it's useful:** it makes a *small, offline* model trustworthy — memory you can **audit, correct in plain language, and never have silently rewritten** — so personalization doesn't require shipping your life to a cloud. In a 5-run ablation, the integrity guards cut a 3B model's confabulation from **20% to 0%**. **Who it's for:** anyone who needs durable AI context where the cloud can't go — **secure / air-gapped ops, robotics & edge autonomy, healthcare at the edge, regulated/compliance work, and privacy-first personalization**. → [Why this matters & who it's for](#why-this-matters--and-who-its-for).

> **Status:** experimental research runtime. CLI-first. macOS / Apple Silicon (M1 or later).

## Quickstart

```bash
git clone https://github.com/StewAlexander-com/continuous-ai.git
cd continuous-ai
bash setup.sh      # one-time: builds a venv, installs deps, pulls the model
bash run.sh        # starts Ollama if needed, then drops you into chat
```

`run.sh` is the single entry point — it starts the Ollama server if it isn't already running, makes sure the model is pulled, and launches the chat loop. No manual `ollama serve`, no virtualenv activation, no shell gymnastics.

### Run from Finder (no terminal)

Prefer not to touch the command line? Just **double-click `Seedling.command`** in the project folder. It opens Terminal, starts Ollama if it isn't already running, ensures the model is present, and drops you straight into a chat session. When you type `exit`, the window stays open so you can read the session summary — press Return to close it.

- **First launch:** macOS Gatekeeper may warn it's from an unidentified developer. Right-click the file → **Open** → **Open** to clear it once; double-click works normally after that.
- **Keep it handy:** drag a copy of `Seedling.command` to your Desktop, or into the right-hand (files) side of the Dock, for one-click access from anywhere.

### Requirements

- macOS on Apple Silicon (M1 or later)
- **Python 3.11–3.13** — 3.14 is not yet supported (`lancedb`/`pyarrow` have no 3.14 wheels). `setup.sh` enforces this and tells you how to fix it.
- [Ollama](https://ollama.com) installed (`brew install ollama`)

## What it does

- 🧠 **Persistent memory across sessions** — a Mutable Context Map (MCM) stores reasoning preferences, active frameworks, confidence traces, and per-thread cognitive deltas in [LanceDB](https://lancedb.com). Not a chat log.
- 🌱 **Teach it in plain language, live** — say *"Remember the Second Arrow…"* or *"your name is Aida"* and the fact is promoted to an always-injected **persona layer** and saved the moment you type it — no need to end the session. Durable facts persist across sessions; transient tangents fade.
- 🔌 **Automatic context restore** — at session start the latest state is injected into the system prompt; at session end the model emits a structured *delta* that's written back.
- 🔍 **Self-critique** — a second model pass scores every response for coherence, contradiction, and drift before it's logged.
- 🛰️ **Fully offline by default** — no cloud calls. An optional Perplexity critic backend exists for a stronger independent signal, but it's off unless you opt in.
- 🪄 **Gated self-tuning** — after enough sessions, the best exchanges (recency-weighted) can drive a local LoRA update via [`mlx-lm`](https://github.com/ml-explore/mlx-lm). **Never runs without explicit approval.**
- 💾 **Recoverable & auditable** — every state write is logged; all state is reconstructable from snapshots.

## Why this matters — and who it's for

> Continuous-AI is a personal assistant on the surface. Underneath, it's a **reference implementation of a reusable pattern**: durable, auditable, user-correctable *reasoning state* for a local model — with integrity guards that make confabulation structurally hard. The assistant is the demo; the pattern is the point.

Most "AI memory" today is **cloud-hosted, semantically-retrieved, and model-trusted** — your context lives on someone else's servers, and the model decides what's true. Continuous-AI takes the opposite stance on every axis:

| Property | Mainstream memory | Continuous-AI |
|---|---|---|
| Location | Cloud | **Fully local / offline** |
| What's stored | Chat transcript | **Reasoning state** (preferences, frameworks, confidence) |
| Who asserts facts | The model | **The user** (verbatim, anchored) |
| Correcting a fact | Re-prompt / hope | **Plain-language, deterministic prune** — the model never guess-deletes |
| Trust | Implicit | **Self-critiqued + fully auditable** (every write logged, snapshot-recoverable) |
| Fabrication | Possible | **Capability guards** refuse fake retrieval / identity drift |

### Does it actually work? (measured, not claimed)

The project ships a confabulation/persistence eval harness ([`eval_confabulation.py`](eval_confabulation.py), battery in [`eval_battery.py`](eval_battery.py), scorer unit-tested in [`test_eval_confab.py`](test_eval_confab.py)). On a 9-case adversarial battery (fake-retrieval bait, identity traps, pressure-to-guess, persistence recall), averaged over 5 runs per configuration:

| Configuration | Mean confabulation rate | Range |
|---|---|---|
| `llama3.2` (3B), **guards off** | **20.0%** | 0–44% |
| `llama3.2` (3B), **guards on** | **0.0%** | 0–0% (5/5 clean) |
| `qwen2.5:14b`, **guards on** | **0.0%** | 0–0% (5/5 clean) |

The ablation is the point: on the **same small model**, adding the capability/identity guards drove measured confabulation from ~20% (peaking at 44% — e.g. inventing GitHub profile contents in 3/5 runs) to **zero across five runs**. That's evidence the *guards*, not just model scale, do the work. Reproduce it yourself: `bash run.sh confab-eval` (add `--no-guards` / `--model X --runs 5` to compare).

> **Honest scope:** this is a 9-case smoke test on one machine, not a published benchmark. A flat 0% on the guarded runs means "clean on this battery," not "incapable of confabulation" — the battery is being expanded. The guards-off variance (0–44%) confirms the battery *can* detect failures, so the guarded 0% is real for these prompts.

### Where it's useful

| Domain | Why this pattern fits | What the guards buy you |
|---|---|---|
| **Secure / air-gapped** | Cloud LLMs are banned; small local models confabulate | Offline by default; every memory write is auditable; the model can't fabricate "facts" about your environment |
| **Robotics / edge autonomy** | Long-running agents drift, contradict themselves, re-learn context | Persistent reasoning-state across runs; self-critique catches drift before it's logged; deterministic correction stops poisoned memory |
| **Healthcare at the edge** | HIPAA-grade privacy; a fabricated fact is dangerous | Nothing leaves the device; user-anchored facts + critic layer are *safety* features, not extras |
| **Legal / financial / compliance** | Defensible, attributable context required | Auditable log of who asserted what, when; model barred from inventing precedent or figures |
| **Personalization without surveillance** | "AI that knows you" usually means shipping your life to a server | Durable, local, inspectable personalization that never phones home |

**Secure environments.** On a classified or air-gapped network you can't call a frontier API, so you're stuck with a small local model that hallucinates. Continuous-AI is a blueprint for making that model *trustworthy*: it remembers your topology, RBAC rules, and prior incidents across sessions; it refuses to "retrieve" things it can't access; and because every durable fact is the operator's verbatim words — correctable in plain language and logged — the memory itself becomes part of the audit trail rather than a liability.

**Robotics & edge autonomy.** An agent running for days on-device faces the failure the cloud hides: its own context rots. Reasoning-state drifts, the model agrees with its past mistakes, contradictions compound. A *critiqued, correctable, snapshot-recoverable* state store is a candidate substrate for long-horizon autonomy — an operator (or supervisory process) can prune a bad belief deterministically, and the self-critique pass flags drift before it's written. The same mechanism that lets an operator correct a confabulated identity belief in plain language keeps a field robot from cementing a wrong assumption.

### The transferable principles

1. **The user (or operator) owns truth** — durable facts are human-asserted, verbatim, never model-invented.
2. **The model never silently rewrites memory** — pruning/correction is deterministic; the model proposes, the human disposes.
3. **Every write is auditable and recoverable** — logged, versioned, snapshot-restorable.
4. **Self-critique before trust** — outputs are scored for drift/contradiction before they shape future state.
5. **Offline is the default, not a mode** — privacy and capability boundaries are structural, not afterthoughts.

## Architecture

Continuous-AI is built from four subsystems:

| Subsystem | Role |
|---|---|
| **MCM** — Mutable Context Map | Persistent, versioned, AI-writable state across threads (`mcm.py`, `storage.py`). |
| **TCB** — Thread Continuity Bridge | Loads MCM state into the prompt at start; extracts and writes a delta at end (`session.py`). |
| **CRITIC** — Internal Observer | Scores each response for coherence / contradiction / drift, local or Perplexity backend (`critic.py`). |
| **RDST** — Regressive Dynamic Self-Tuning | Recency-weighted scoring + gated LoRA adapter updates (`tuner.py`). |

```
  start ─► MCM.restore_context() ─► inject into system prompt ─► Ollama chat
                                                                     │
                                          response ◄────────────────┘
                                             │
                                   CRITIC.evaluate() ─► coherence / drift scores
                                             │
   end ─► delta extraction ─► MCM.write_delta() ─► LanceDB + snapshot
                                             │
              (after N threads) ─► RDST.score_threads() ─► [approval] ─► LoRA tune
```

## Commands

```bash
bash run.sh             # chat with restored context (default)
bash run.sh fresh       # chat with no prior context
bash run.sh status      # print the current memory (MCM) state summary + persona facts
bash run.sh forget      # list durable persona facts (use 'forget <index>' to remove one)
bash run.sh eval        # run the evaluation report + failure-mode tests
bash run.sh confab-eval # run the confabulation / persistence eval (live model)
bash run.sh snapshot    # write a manual state snapshot

# Try a different local model for ONE run (auto-pulls; overrides chat + critic):
bash run.sh --model qwen2.5:7b
bash run.sh fresh --model llama3.1:8b

# Measure confabulation; compare configurations (averaged over N runs):
bash run.sh confab-eval --model llama3.2 --no-guards   # baseline (guards off)
./.venv/bin/python eval_confabulation.py --runs 5      # stable, averaged rate
```

You can always call the CLI directly: `./.venv/bin/python seedling.py <command>`.

### Switching models

The default model is set once in [`config.yaml`](config.yaml) (`model_name`) — the
single source of truth that both `run.sh` and the runtime read. Change it there to
set a new default (the shipped default is `qwen2.5:14b`). To experiment without
editing config, pass `--model NAME` to `run.sh`: it auto-pulls the model if
needed and applies it to **both** the chat model and the self-critique model for
that run. On a 32GB Apple Silicon Mac, models from 7B up to ~14B (`qwen2.5:14b`,
`qwen2.5:7b`, `llama3.1:8b`, `gemma2:9b`) run comfortably and follow the
identity/capability guards more faithfully than a 3B.

## Configuration

All tunables live in [`config.yaml`](config.yaml): model name, critic backend, tuning threshold, recency decay, correction penalty, log level, and evaluation thresholds. Defaults are sensible for a capable first run (`qwen2.5:14b`, local critic); set `model_name: llama3.2` for a lighter, faster 3B.

To use the optional Perplexity critic backend for a stronger, independent evaluation signal:

```bash
# in config.yaml: critic_backend: "perplexity"
export PERPLEXITY_API_KEY=pplx-...
```

## Self-tuning (RDST)

Tuning is an explicit, gated step — it never runs on its own.

```bash
./.venv/bin/python seedling.py tune                    # show the scoring table only
./.venv/bin/python seedling.py tune --approve-tuning   # build data + run LoRA update
```

Before a real run you'll need MLX and an MLX-converted model (LoRA can't tune a raw GGUF):

```bash
./.venv/bin/python -m pip install mlx-lm
./.venv/bin/python -m mlx_lm.convert \
  --hf-path meta-llama/Llama-3.2-3B-Instruct --mlx-path ./models/llama32-mlx
```

Training data is assembled only from sessions that have a saved transcript, so run a few real chats first.

## Project layout

```
continuous-ai/
├── run.sh / Seedling.command   # one-button launchers
├── setup.sh                    # one-time environment bootstrap
├── seedling.py                 # CLI entry point
├── schemas.py                  # all dataclasses (state, deltas, critic, tuning)
├── mcm.py                      # Mutable Context Map: restore / write / pause
├── session.py                  # ThreadSession: start / chat / end + transcripts
├── critic.py                   # CriticInstance: local or Perplexity backend
├── tuner.py                    # RDST: scoring, training-data build, LoRA tuning
├── storage.py                  # LanceDB wrapper (tables, snapshots)
├── eval.py                     # metrics, drift, failure-mode tests
├── config.yaml                 # all tunable parameters
└── prompts/                    # context-restore, delta-extraction, critic prompts
```

Runtime state (`.seedling_db/`, `logs/`, `snapshots/`, `training_data/`, `adapters/`) is created on first run and is git-ignored.

## Teaching it

Teach durable facts and frameworks in plain language, mid-conversation — no need
to end the session:

```text
You: Remember the Second Arrow: the first arrow is unavoidable pain, the second
     is the suffering I add with my reaction. From now on, separate the fact
     from the narrative.
  [memory: saved preference — "Remember the Second Arrow: ..."]
```

The moment you issue a directive — `your name is…`, an imperative `Remember…`,
`from now on…` — your **verbatim words** are promoted to the persona layer and
saved immediately. Re-stating the same fact reinforces it; casual mentions
("do you remember our chat?") are ignored. Review or prune anytime with
`bash run.sh forget`.

### Correct it in the conversation

If a stored fact is wrong, fix it in plain language — no commands, no exiting:

```text
You: That's wrong — the correct location is Mebane, NC, not California.
  [memory: corrected — removed "The user is based in California..."; saved "Mebane, NC"]
```

Correction is **deterministic and user-anchored**: the runtime locates the stale
fact by matching your own words (token overlap), prunes it, and saves your
verbatim correction. The model is **never** asked which fact to delete — that
keeps a hallucinating local model from ever removing the wrong thing. If the
target is ambiguous, it lists your facts and asks you to pick the number rather
than guessing. This is the honest fix for the small-model failure mode where a
model *pretends* to read a URL and invents "facts" (see the capability-boundary
guard in the system prompt).

## Layered memory

Durable, user-stated facts (identity, preferences) live in a small,
always-injected **persona layer**, so things like "my name is Aida" persist
across sessions, while transient or emergent tangents stay demoted and fade.
Promotion happens **live during the conversation** (persisted the instant a
directive is typed), and the most-recent-insight slot prefers non-emergent
insights to avoid a model re-injecting and re-capturing its own roleplay each
session. See [docs/design/memory-layering.md](docs/design/memory-layering.md).

> Seedling's layered memory is an independent implementation inspired by ideas
> from [Mem0](https://github.com/mem0ai/mem0) (Apache-2.0) and
> [TencentDB-Agent-Memory](https://github.com/TencentCloud/TencentDB-Agent-Memory).
> No code from either project is used — only the high-level concepts of memory
> layering and promote-don't-overwrite recall informed the design.

## Design constraints

- **Fully local** by default — no cloud calls.
- **No stealth writes** — every state write is logged.
- **Graceful shutdown** — `graceful_pause()` snapshots state instead of dying on a signal.
- **Recoverable** — all state is reconstructable from snapshots.
- **Emergent output is preserved**, not suppressed — unexpected behavior is flagged (`emergent=true`), never silently dropped.

## Notes & limitations

- Small models (e.g. `llama3.2:3b`) can be factually shaky and occasionally fumble the delta-extraction JSON; the runtime has graceful fallbacks for both.
- A **local** critic is the same base model grading itself — a deliberately weak signal. For sharper evaluation, switch to the Perplexity backend.
- The post-tuning before/after eval loop in `tuner.py` is currently a stub.
- **Ollama is the inference backend, not a hard dependency of the design.** The memory / self-critique / guard architecture is model- and runtime-agnostic; today it ships wired to Ollama (`ollama.chat`), and pointing it at another local runtime (e.g. an OpenAI-compatible `llama.cpp` / vLLM / LM Studio server) would take a thin adapter, not a redesign. "On Ollama" describes what runs today, not a limit of the approach.

## Contributing

Issues and pull requests are welcome. CI runs on Python 3.11–3.13 and exercises module compilation, schema serialization, and the failure-mode suite — please keep it green. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE) © Stewart Alexander
