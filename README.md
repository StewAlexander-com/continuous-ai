# Continuous-AI

**A local, fully offline AI runtime that remembers across sessions, critiques its own answers, and can self-tune — all on Apple Silicon, no cloud required.**

Most local LLM setups are amnesiacs: every chat starts from zero. Continuous-AI adds a persistent, versioned, machine-writable memory layer on top of [Ollama](https://ollama.com) so the model carries its *reasoning state* — not just a chat log — from one session to the next. A second model pass scores every answer for coherence and drift, and that signal can drive optional LoRA fine-tuning.

> Status: experimental research runtime. CLI-first. Runs on macOS / Apple Silicon (M1 or later).

---

## Quickstart (clone and run)

```bash
git clone https://github.com/StewAlexander-com/continuous-ai.git
cd continuous-ai
bash setup.sh      # one-time: builds a venv, installs deps, pulls the model
bash run.sh        # starts Ollama if needed, then drops you into chat
```

That's it. `run.sh` is the single entry point — it starts the Ollama server if it isn't already running, makes sure the model is pulled, and launches the chat loop. No manual `ollama serve`, no virtualenv activation, no shell gymnastics.

**Prefer a button?** On macOS, double-click `Seedling.command` in Finder. (First launch: right-click → Open to clear the unidentified-developer prompt.)

### Requirements

- macOS on Apple Silicon (M1 or later)
- **Python 3.11–3.13** (3.14 is not yet supported — `lancedb`/`pyarrow` have no 3.14 wheels; `setup.sh` enforces this and tells you how to fix it)
- [Ollama](https://ollama.com) installed (`brew install ollama`)

---

## Commands

```bash
bash run.sh             # chat with restored context (default)
bash run.sh fresh       # chat with no prior context
bash run.sh status      # print the current memory (MCM) state summary
bash run.sh eval        # run the evaluation report + failure-mode tests
bash run.sh snapshot    # write a manual state snapshot
```

You can always call the CLI directly: `./.venv/bin/python seedling.py <command>`.

---

## What it does

Continuous-AI is built from four subsystems:

| Subsystem | What it is |
|---|---|
| **MCM** — Mutable Context Map | Persistent, versioned, AI-writable state across threads. Not a chat log — it stores reasoning preferences, active frameworks, confidence traces, and per-thread cognitive deltas. |
| **TCB** — Thread Continuity Bridge | At session start, loads the latest MCM state and injects it into the system prompt. At session end, prompts the model to emit a structured *delta* and writes it back. |
| **CRITIC** — Internal Observer | A second model pass that scores each response for coherence, contradiction, and drift before it's logged. Runs locally (Ollama base model) or against the Perplexity API for a genuinely independent signal. |
| **RDST** — Regressive Dynamic Self-Tuning | After enough threads, scores the best exchanges (recency-weighted) and can run a LoRA adapter update via `mlx-lm`. **Manual approval gate — never auto-tunes.** |

### Data flow

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

---

## Design constraints (by design)

- **Fully local.** No cloud calls by default. (An optional Perplexity critic backend exists for a stronger, independent evaluation signal; it's off unless you set `critic_backend: perplexity` and a `PERPLEXITY_API_KEY`.)
- **No stealth writes.** Every state write is logged.
- **Graceful shutdown.** `graceful_pause()` snapshots state instead of dying on a signal.
- **Recoverable.** All state is reconstructable from snapshots in `snapshots/`.
- **Emergent output is preserved**, not suppressed — unexpected behavior is flagged (`emergent=true`), never silently dropped.

---

## Configuration

All tunables live in [`config.yaml`](config.yaml): model name, critic backend, tuning threshold, recency decay, correction penalty, log level, and evaluation thresholds. Defaults are sensible for a first run (`llama3.2`, local critic).

---

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

---

## Optional: self-tuning (RDST)

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

---

## Notes & limitations

- Small models (e.g. `llama3.2:3b`) can be factually shaky and occasionally fumble the delta-extraction JSON; the runtime has graceful fallbacks for both.
- A **local** critic is the same base model grading itself — a deliberately weak signal. For sharper evaluation, switch to the Perplexity backend.
- The post-tuning before/after eval loop in `tuner.py` is currently a stub.

---

## License

MIT — see [LICENSE](LICENSE).
