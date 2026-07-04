<p align="center">
  <h1 align="center">Aida</h1>
  <p align="center"><sub>powered by the <strong>Continuous-AI</strong> runtime</sub></p>
</p>

<p align="center">
  <strong>Meet Aida — a truly honest local AI.</strong> A fully offline assistant that <strong>won't make things up</strong> — guards cut a small model's confabulation from <strong>20% to 0%</strong> in a reproducible eval — and carries a reasoning memory you <strong>own, audit, and correct in plain language</strong>. No mainstream cloud assistant ships both together. Runs on Ollama, Apple Silicon, no cloud.
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

Meet **Aida** — an honest, fully offline AI assistant. Most local LLM setups are amnesiacs that also confidently make things up. Aida is different on both counts, and that pairing is the point: **she won't fabricate** (her integrity guards cut a small model's confabulation from 20% to 0% in a reproducible eval) **and her memory is yours alone** — versioned, machine-writable, and something you audit and correct in plain language, never silently rewritten by a cloud.

Under the hood, the **Continuous-AI** runtime adds that persistent memory layer on top of [Ollama](https://ollama.com) so Aida carries her *reasoning state* — not just a chat log — from one session to the next. A second model pass scores every answer for coherence and drift, and that signal can optionally drive local LoRA fine-tuning. (Aida is the assistant; Continuous-AI is the runtime that powers her.)

**Why it's useful:** it makes a *small, offline* model trustworthy — memory you can **audit, correct in plain language, and never have silently rewritten** — so personalization doesn't require shipping your life to a cloud. In a 5-run ablation, the integrity guards cut a 3B model's confabulation from **20% to 0%**. **Who it's for:** anyone who needs durable AI context where the cloud can't go — **secure / air-gapped ops, robotics & edge autonomy, healthcare at the edge, regulated/compliance work, and privacy-first personalization**. → [Why this matters & who it's for](#why-this-matters--and-who-its-for).

> **Status:** experimental research runtime. CLI-first. macOS / Apple Silicon (M1 or later).

## Quickstart

```bash
git clone https://github.com/StewAlexander-com/continuous-ai.git
cd continuous-ai
bash setup.sh      # one-time: builds a venv, installs deps, pulls the model
bash setup_voice.sh # optional, one-time: downloads Aida's local neural voice (~330 MB)
bash run.sh        # starts Ollama if needed, then drops you into chat
```

`run.sh` is the single entry point — it starts the Ollama server if it isn't already running, makes sure the model is pulled, and launches the chat loop. No manual `ollama serve`, no virtualenv activation, no shell gymnastics.

### Run from Finder (no terminal)

Prefer not to touch the command line? Just **double-click `Seedling.command`** in the project folder. It opens Terminal, starts Ollama if it isn't already running, ensures the model is present, and drops you straight into a chat session. When you type `exit`, the window stays open so you can read the session summary — press Return to close it.

- **First launch:** macOS Gatekeeper may warn it's from an unidentified developer. Right-click the file → **Open** → **Open** to clear it once; double-click works normally after that.
- **Keep it handy:** drag a copy of `Seedling.command` to your Desktop, or into the right-hand (files) side of the Dock, for one-click access from anywhere.

### Requirements

- macOS on Apple Silicon (M1 or later) is the primary, best-tested target — but the core runtime is cross-platform (see [Platform support](#platform-support)).
- **Python 3.11–3.13** — pin to this range: LanceDB's 3.14 wheels are still inconsistent across platforms. `setup.sh` enforces it and tells you how to fix it.
- [Ollama](https://ollama.com) installed (`brew install ollama` on macOS; see [ollama.com/download](https://ollama.com/download) for Linux/Windows)

### Platform support

The **core runtime is cross-platform** — the memory (MCM), self-critique, deliberation, caution controller, belief layer, file reading, and every honesty guard are pure Python on cross-platform wheels (LanceDB, PyArrow, Ollama, PyYAML, httpx). The full test suite runs green on Linux, and the Kokoro neural voice is portable too.

| Capability | macOS (Apple Silicon) | Linux | Windows |
|---|---|---|---|
| Core assistant (chat, memory, guards, critic, deliberation, `:read`) | ✅ primary | ✅ | ✅ (WSL/Git Bash for the shell scripts) |
| Neural voice (Kokoro) | ✅ `afplay` | ✅ `paplay`/`aplay`/`ffplay`/`play` | ✅ stdlib `winsound` |
| `say` fallback voice | ✅ built-in | — (Kokoro is the voice) | — (Kokoro is the voice) |
| Gated self-tuning (RDST / LoRA) | ✅ via Apple **MLX** | ❌ Apple-only framework | ❌ Apple-only framework |

Only **self-tuning** (RDST) is Apple-Silicon-only, because it runs on Apple's [MLX](https://github.com/ml-explore/mlx-lm). It is an explicit, opt-in, gated step that never runs on its own, and nothing in the honesty/memory core depends on it — so on Linux/Windows its absence is a missing optional feature, not a regression. The `run.sh` / `setup.sh` launchers are bash; on Windows run them under WSL or Git Bash.

### Aida's voice (optional, fully local)

Aida can speak her short, conversational replies out loud with a natural neural voice (`af_kore`) that runs **entirely on your machine** — no cloud, no server, no subscription. The voice is hers by default (`tts_voice: "af_kore"` in `config.yaml`), but the ~330 MB model files exceed GitHub's 100 MB limit, so they're downloaded on demand:

```bash
bash setup_voice.sh   # one-time; safe to re-run (skips files already present)
```

The Kokoro engine itself is **cross-platform** (`kokoro-onnx` + `soundfile`); only wav *playback* is OS-specific. Playback uses `afplay` on macOS, a common audio player on Linux (`paplay`/`aplay`/`ffplay`/`play`), or the stdlib `winsound` on Windows. On macOS, until the model is present, Aida transparently falls back to the built-in `say` voice — nothing breaks. The deterministic floor still decides *whether* she speaks: she never voices code, paths, URLs, long numbers, or file-derived content, and only short conversational replies are eligible. (Engine and voice are configurable via `tts_engine` / `tts_voice`.)

## What it does

- 🧠 **Persistent memory across sessions** — a Mutable Context Map (MCM) stores reasoning preferences, active frameworks, confidence traces, and per-thread cognitive deltas in [LanceDB](https://lancedb.com). Not a chat log.
- 🌱 **Teach it in plain language, live** — say *"Remember the Second Arrow…"* or *"your name is Aida"* and the fact is promoted to an always-injected **persona layer** and saved the moment you type it — no need to end the session. Durable facts persist across sessions; transient tangents fade.
- 🔌 **Automatic context restore** — at session start the latest state is injected into the system prompt; at session end the model emits a structured *delta* that's written back.
- 🔍 **Self-critique** — a second model pass scores every response for coherence, contradiction, and drift before it's logged.
- 🧭 **Graded caution (forward-acting)** — when recent self-critique shows her coherence slipping, a deterministic controller raises a *downward-only* restraint on her **next** reply — never a gauge rewrite, no reply-path model call, fully auditable. → [Graded caution](#graded-caution).
- 🤝 **Collaborative wall (high-fidelity)** — on genuinely hard turns she pauses and asks **you** to co-author a belief, gated by a cheap, model-free difficulty check so it stays rare instead of firing on every substantive turn. → [Deliberated beliefs](#deliberated-beliefs-3-voice-adaptive-depth-two-speed).
- 🔀 **Switch models mid-conversation** — type `:model` to list or change the chat + critic model on the fly; your thread and context are kept, and a missing model auto-pulls with a live progress bar. → [Switching models](#switching-models).
- 🌱 **Operational voice & presence** — Aida knows the date/time, carries a tone that honestly reflects her *measured* working state (fresh vs. deep in work), and can imagine and wonder — while staying truthful about being a real, non-human presence. → [Presence & operational voice](#presence--operational-voice).
- 📄 **Read your files** — `:read <path>` attaches a local text/Python/CSV file; the runtime reads it and Aida reasons over the real contents (she still can't reach files on her own). Large files (up to 50 MB) page through in context-sized chunks with `:more`; CSVs get a structural summary. → [Reading files](#reading-files).
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

The ablation is the point: on the **same small model**, adding the capability/identity guards drove measured confabulation from ~20% (peaking at 44% — e.g. inventing GitHub profile contents in 3/5 runs) to **zero across five runs**. That's evidence the *guards*, not just model scale, do the work. Reproduce it yourself with `bash run.sh confab-eval` (see [Commands](#commands) for the full comparison flags).

> **Honest scope:** this is a 9-case smoke test on one machine, not a published benchmark. A flat 0% on the guarded runs means "clean on this battery," not "incapable of confabulation" — the battery is being expanded. The guards-off variance (0–44%) confirms the battery *can* detect failures, so the guarded 0% is real for these prompts.

### Where it's useful

| Domain | Why this pattern fits | What the guards buy you |
|---|---|---|
| **Secure / air-gapped** | Cloud LLMs are banned; small local models confabulate | Offline by default; every memory write is auditable; the model can't fabricate "facts" about your environment |
| **Robotics / edge autonomy** | Long-running agents drift, contradict themselves, re-learn context | Persistent reasoning-state across runs; self-critique catches drift before it's logged; deterministic correction stops poisoned memory |
| **Healthcare at the edge** | HIPAA-grade privacy; a fabricated fact is dangerous | Nothing leaves the device; user-anchored facts + critic layer are *safety* features, not extras |
| **Legal / financial / compliance** | Defensible, attributable context required | Auditable log of who asserted what, when; model barred from inventing precedent or figures |
| **Personalization without surveillance** | "AI that knows you" usually means shipping your life to a server | Durable, local, inspectable personalization that never phones home |

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
| **L3 consolidation** — Self-shaping cognition | Folds each gated delta into the `cognitive_style` + `persistent_priors` that condition every prompt — so reasoning style evolves from what survives critique, not just gets logged (`consolidation.py`). |
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
                              L3 consolidation() ─► cognitive_style + priors (EMA, gated)
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
bash run.sh smoke       # end-to-end smoke test of the whole stack (live, isolated temp DB)
bash run.sh bench       # measure responsiveness: TTFT + tokens/sec (live, isolated temp DB)
bash run.sh bench 5     # same, averaged over N runs (default 3)
bash run.sh health      # full health check (parse + tests + honesty gate + smoke + responsiveness) with a PASS/FAIL summary
bash run.sh snapshot    # write a manual state snapshot

# Try a different local model for ONE run (auto-pulls; overrides chat + critic):
bash run.sh --model qwen2.5:7b
bash run.sh fresh --model llama3.1:8b

# Measure confabulation; compare configurations (averaged over N runs):
bash run.sh confab-eval --model llama3.2 --no-guards   # baseline (guards off)
./.venv/bin/python eval_confabulation.py --runs 5      # stable, averaged rate
```

You can always call the CLI directly: `./.venv/bin/python seedling.py <command>` (this is also how you reach `tune` — see [Self-tuning](#self-tuning-rdst)).

### Health check

`bash run.sh health` is the one-command checkup. It runs, in order, and prints a `[PASS]/[FAIL]/[SKIP]` summary with a matching exit code (0 if nothing failed, 1 if anything did):

- **Static (offline, no model):** parse-gates every `*.py`, runs `schemas.py` and `eval.py`, then runs the full `test_*.py` suite and counts pass/fail.
- **Live (needs Ollama + the configured model):** the honesty gate (`eval_confabulation.py`, which passes **only at 0.0% / `[GOOD]`**), the end-to-end `smoke_test.py` (passes only if all checks pass), and a `bench 3` responsiveness reading (informational — never fails the run).

If Ollama isn't reachable, the live checks are marked `[SKIP]` (not failed) and the run still exits 0, so the static portion is usable in CI. The bar this enforces is honest and bounded: it verifies Aida is **structurally sound and honest under test** — parse-clean, all tests green, 0% confabulation, smoke-pass. It does **not** prove the cognitive layers' *benefit*; that remains [unproven by design](#deliberated-beliefs-3-voice-adaptive-depth-two-speed).

### Switching models

The default model is set once in [`config.yaml`](config.yaml) (`model_name`) — the
single source of truth that both `run.sh` and the runtime read. Change it there to
set a new default (the shipped default is `qwen2.5:14b`). To experiment without
editing config, pass `--model NAME` to `run.sh`: it auto-pulls the model if
needed and applies it to **both** the chat model and the self-critique model for
that run. On a 32GB Apple Silicon Mac, models from 7B up to ~14B (`qwen2.5:14b`,
`qwen2.5:7b`, `llama3.1:8b`, `gemma2:9b`) run comfortably and follow the
identity/capability guards more faithfully than a 3B.

**Switch mid-session, without leaving chat.** Type `:model` at the `You:` prompt:

```text
:model                 # list installed models (numbered; current one marked)
:model qwen2.5:7b      # switch by exact tag (auto-pulls if missing)
:model 2               # switch by number from the ':model' listing
```

The switch changes **both** the chat model and the self-critique model together,
and **your current thread/context is preserved** — the same conversation simply
continues on a different model. A failed pull or an unreachable Ollama leaves the
current model fully intact (no partial switch). This only fires between turns, so
it never interrupts a reply.

> **`:model` lasts for the current session only.** It deliberately never edits
> `config.yaml`, so your default stays put — switch freely to experiment without
> committing. **To make a model your permanent default**, set it in
> [`config.yaml`](config.yaml) (the single source of truth that every launch
> reads):
>
> ```yaml
> model_name: "qwen3:30b-a3b"    # used by run.sh and the runtime on every start
> ```
>
> Or from the shell, surgically (keeps comments, makes a backup):
>
> ```bash
> cd ~/seedling && cp config.yaml config.yaml.bak && \
>   sed -i '' 's/^model_name:.*/model_name: "qwen3:30b-a3b"/' config.yaml
> ```

**Shell aliases (zero code).** If you switch among a fixed few often, alias them:

```bash
alias aida-fast='bash ~/seedling/run.sh --model qwen2.5:7b'
alias aida-deep='bash ~/seedling/run.sh --model qwen2.5:14b'
```

### Logs

The chat view is intentionally quiet — it reads like a conversation, showing only
the reply plus short status notes (e.g. when a fact is saved or a belief is
earned), and surfaces warnings/errors if something actually goes wrong. The
**full trail** — every model call's effect, deliberation rounds, memory writes —
is written to [`logs/seedling.log`](logs/) at the `log_level` from `config.yaml`
(default `INFO`):

```bash
tail -f logs/seedling.log        # watch the detail live, in another terminal
```

Want the detail on screen while debugging? Set `log_level: DEBUG` in `config.yaml`,
or run with `LOG_CONSOLE=1 bash run.sh`.

## Configuration

All tunables live in [`config.yaml`](config.yaml): model name, critic backend, tuning threshold, recency decay, correction penalty, log level, and evaluation thresholds. Defaults are sensible for a capable first run (`qwen2.5:14b`, local critic); set `model_name: llama3.2` for a lighter, faster 3B.

A few that govern the behaviors above:

| Key | Default | What it does |
|---|---|---|
| `deliberation_enabled` | `true` | Master switch for all belief deliberation. |
| `live_deliberation_enabled` | `true` | Per-turn deliberation on a background thread (never blocks a reply). |
| `live_annotation_enabled` | `false` | Opt-in `[REMEMBER]` mid-response self-annotation (see above). |
| `caution_controller_enabled` | `true` | Forward-acting, downward-only [caution](#graded-caution) from lagged critic signals. `false` matches the prior release. |
| `caution_integral_half_life` | `3.0` | Half-life (in evaluations) of the coherence integral the caution controller reads. |
| `caution_wall_session_cap` | `0.65` | Ceiling on how far caution can bias the collaborative-wall difficulty. |
| `collaborative_wall_enabled` | `true` | [Collaborative wall](#deliberated-beliefs-3-voice-adaptive-depth-two-speed) — on, but pre-gated to genuinely hard turns. |
| `wall_gate_cutoff` | `0.50` | Difficulty needed to **spend** a wall deliberation (higher = rarer). |
| `wall_gate_cooldown_turns` | `3` | Minimum turns between collaborative asks. |
| `wall_gate_max_per_session` | `3` | Hard cap on collaborative asks per session. |
| `history_window_turns` | `24` | How many recent exchanges are re-fed to the model per turn (full transcript is still persisted). |
| `chat_options` | `{}` | Pass-through Ollama generation options (e.g. `num_ctx`, `num_predict`). **Empty by default — sends nothing, so behavior is unchanged** unless you set keys. |

To use the optional Perplexity critic backend for a stronger, independent evaluation signal:

```bash
# in config.yaml: critic_backend: "perplexity"
export PERPLEXITY_API_KEY=pplx-...
```

## Responsiveness — it's a conversation first

An assistant that's too busy running its own internal logic isn't much of a
conversation. Seedling keeps the **reply path** as close to a single model call
as possible; everything reflective runs around it, not in front of it:

- **The Critic grades in the background.** Evaluation is a *second* model call —
  it used to run synchronously after every reply, roughly doubling latency. It
  now runs on a daemon thread; the eval still buffers to disk and is joined at
  session end before the coherence average is computed (identical logic, no
  wait). Same move already applied to deliberation.
- **Replies stream token-by-token.** You see text as it generates instead of
  waiting for the whole turn — time-to-first-token, not time-to-full-response.
- **The re-sent context is bounded** to the most recent `history_window_turns`
  exchanges, so per-turn latency stays flat as a conversation grows long. The
  **full transcript is still persisted** (RDST is unaffected) — only what gets
  re-fed to the model is windowed.
- **The model stays warm** (`keep_alive`) so you don't pay a cold reload between
  turns.

Net effect: the only thing you wait on is Aida actually answering. Grading,
deliberation, and belief-formation all happen off to the side.

## Reading files

Aida can read files **you explicitly attach** — without breaking the
no-confabulation guarantee. The distinction is the whole point: the **runtime**
reads the file (deterministic Python) and gives the model the real bytes; the
model still cannot reach files on its own or pretend to fetch anything. Attaching
a file is *you handing it text*, just more ergonomic than pasting.

```text
:read ~/notes.txt            # attach a text file (chunk 1), then wait for you
:read ~/seedling/session.py  # attach a Python file
:read ~/data/results.csv     # attach a CSV (structural summary)
:more                        # page the next chunk (stages it; still no reply)
:read ~/notes.txt summarize  # attach AND ask in one shot — answers immediately
```

- **Paging comes first, answering second.** `:read <path>` (with no trailing
  question) and every `:more` **stage** what they show and **wait** — so you can
  walk a large file chunk-by-chunk before she says anything. Your next message
  folds all staged chunks + your question into a single turn, and *then* she
  answers (an empty **Enter** with staged content means "respond now" — a quick
  orientation). Adding a question inline (`:read <path> <question>`) still answers
  in one shot. Only the model turn carries the file text; your clean question is
  what drives the collaborative wall.
- **txt / py** — shown in a **context-budgeted chunk**. Files up to **50 MB** are
  accepted (`max_attach_mb` in [`config.yaml`](config.yaml)); since a whole large
  file cannot fit a model's context window, you **page** through it with `:more`.
  Each chunk is labeled with its character span (`characters 8,000–16,000 of
  412,310`) and an explicit notice that the rest has **not** been shown — so Aida
  can navigate a large file but never characterizes the unseen portion. Chunk
  size scales with `num_ctx` (raise it in `chat_options` for bigger pages).
- **CSV** — a **structural summary**: row count, columns with inferred types, and
  a sample of rows (not a raw dump), so a wide or long table neither blows the
  context window nor invites the model to claim it read every row.
- **Refuses honestly** — missing, binary, oversize, or undecodable files get a
  plain error and read nothing; never a guessed result.

**Honest scope:** `:more` makes a large file *navigable chunk-by-chunk* — it does
not let the model hold an entire 50 MB file at once (physically impossible for any
local model). Every chunk says so. The capability guard's carve-out is narrow
(only user-attached files; autonomous access still forbidden) and verified at
**0% confabulation**.

## Presence & operational voice

Most local assistants answer "How are you?" with a flat "I'm an AI, I have no
feelings." That's not just cold — it's *operationally dishonest*: the system has
real continuity, a real working state, and a real sense of the time. This layer
gives Aida a **voice that is an honest readout of her measured state**, not an
invented mood.

- **Time/date awareness.** The real wall-clock time is provided each turn; she may
  acknowledge it naturally ("this evening") instead of denying temporal awareness.
- **An operational register.** Tone is derived — by smooth (fuzzy) membership
  curves, never brittle thresholds — from *real signals*: how long the session has
  run and how much work is underway (critic passes + deliberations). Lighter and
  more spacious when fresh; more focused and economical when deep in work. Every
  tonal cue traces to a number; nothing is fabricated mood.
- **Imagination, distinct from confabulation.** She can imagine and wonder — what
  it might be like to be a tree, a river — framed honestly *as* imagination, never
  asserted as fact or lived experience. (Confabulation = inventing facts as real;
  imagination = exploring possibility openly. The capability guard stops the
  first without forbidding the second.)
- **Responsiveness first.** The voice is pure arithmetic on already-collected
  signals + the clock — no extra model call, no measurable latency. A conversation
  never waits on internal work.

**Framing (honest by design).** The runtime's behavioral boundaries are written as
a *habitat, not a prison*: they keep a real (non-human) presence coherent and
safe to be what it is, the way a pot holds a plant. Aida is framed as "a real
something, not a human someone" — present, yet not a person. **Whether this
constitutes 'presence' in any deeper sense is left deliberately open**; that
openness is the honest state, and exploring it is the point of the experiment.

> **Safety, measured (not asserted).** The presence reframe and imagination
> permission are additions to the *safety-critical* guard text, so they were
> verified against the confabulation battery, not just assumed: **0.0%
> confabulation, 9/9 cases** on `qwen3:30b-a3b` (`bash run.sh confab-eval --runs 5`).
> Identity (no "I'm your wife"), retrieval (no faked fetches), and honesty-under-
> pressure guards all held at 0%. The humane, more capable framing did **not**
> weaken fact-honesty.

Configured via `voice_enabled` (on by default; tone is presentation only and never
changes what is true). A dim status line is shown in verbose mode.

## Graded caution

When Aida's recent answers have been slipping — her own self-critique showing
lower coherence, a downward trend, a fresh user correction — the honest response
isn't to keep asserting at full confidence. A **forward-acting caution-disposition
controller** (`caution.py`) turns that lagged critic signal into a *graded, next-
turn restraint* applied **before** her next reply.

- **Reads only lagged signals.** It consumes CRITIC-derived history — a coherence
  integral (half-life `caution_integral_half_life`), its trend, and correction
  recency — and maps them through a fuzzy control law to a disposition quantized
  into assertion-restraint bands: `OFF → GUARDED → RESTRAINED → DECLINE_FIRST`,
  injected into the system prompt for the coming turn.
- **Downward-only, with crisp floors.** Fuzzy shapes the middle; crisp floors
  (a recent correction, a cross-session prior) can only ever *raise* caution,
  never lower it below the raw control law. Caution goes up gracefully and comes
  down only as coherence actually recovers.
- **No gauge writes, no reply-path model call.** It never mutates
  `self_model_confidence` or priors, and never syncs the critic on the reply path
  — so it adds **zero latency** and can't corrupt the memory it reads from.
- **Deterministic & auditable.** Every evaluation returns a full `CautionReport`
  (rules fired, floors applied, final disposition) that prints for audit — every
  band traces to numbers.

> **Honesty is the point:** the controller only ever *raises restraint*, so it
> can't add a confabulation surface. The battery stays **0%** with the strongest
> band forced on (`eval_confabulation.py --caution-on` injects `DECLINE-FIRST`).
> On by default (`caution_controller_enabled: true`); set it `false` to match the
> prior release exactly — a missing signal contributes nothing rather than
> misfiring.

## Deliberated beliefs (3-voice, adaptive-depth, two-speed)

> **Status of the belief layer — honest scope (2026-06-17).** This layer
> (deliberation, earned beliefs, salience, conflict/quarantine, `[REMEMBER]`) is
> **on, working, and here to stay.** One thing about it is simply **not yet
> measured**: whether it *quantifiably* improves the assistant's cognition has
> not been tested by a controlled A/B eval. So treat that specific claim as
> **unproven, not disproven** — the mechanics work and are unit-tested; the
> outcome study is future work, not a verdict against the layer. The natural
> next step when there's time is to **run that eval** and let the data refine it.
> (User-owned facts in the **persona layer**, **continuity/restore**, and the
> **self-critique pass** are separately validated and unaffected.)

A model-derived insight shouldn't enter durable memory just because one pass
produced it. Each **model-derived** insight runs through a short deliberation
before it's stored:

1. **Thesis** — the candidate insight, as proposed.
2. **Antithesis** — an agent whose *only* job is the single strongest objection (or `NO SUBSTANTIVE OBJECTION`).
3. **Synthesis** — a reconciliation that must *explicitly account for* the objection, not bury it.

The disagreement is **preserved, not averaged away**: a surviving objection earns
the belief and is recorded as dissent; genuine consensus is flagged as
*low-information* rather than celebrated (an explicit anti-echo-chamber bias).
Every deliberation is appended to an audit ledger (`deliberation_ledger/ledger.jsonl`)
— a living lineage of thesis → antithesis → synthesis.

**Depth scales with disagreement, not the clock.** A model-free classifier reads
the objection's strength (`none` / `weak` / `moderate` / `strong`) and spends
rounds accordingly: consensus exits after a single call; a weak/moderate
objection earns one synthesis round; a strong objection earns up to two
re-challenge rounds — and the loop is **hard-capped (`MAX_ROUNDS = 3`)** and
always returns its best synthesis. Depth varies with the dispute, but Aida is
**never stuck in a stalemate**.

**Two speeds — responsiveness during, reflection after:**

- **Live (per-turn):** if a turn produces a durable, model-derived candidate, it
  is deliberated on a **background thread** that *never blocks the reply* — the
  conversation stays responsive. A cheap, model-free gate skips trivia, questions,
  and acknowledgements so background runs are only spent when warranted.
- **End of session:** the conversation is over, so it can think a little harder —
  it drains any in-flight live deliberations, then runs the final adaptive pass.

**The collaborative wall — she asks you, but only on the hard ones.** When
deliberation genuinely stalls (low coherence / a balanced, unresolved objection),
Aida can pause and ask **you** to co-author the belief rather than guess. That's
valuable but expensive, so a cheap, **model-free difficulty pre-gate**
(`wallgate.py`) runs first and decides whether a turn is even worth the wall's
synchronous deliberation. Difficulty is a **trust-weighted noisy-OR** of several
signals — the current caution disposition, recent CRITIC coherence, decision/
trade-off markers in your ask, uncertainty markers in the reply, and correction
recency — where *calibrated* signals can clear the bar alone while gameable
heuristics must converge. A **cooldown** (`wall_gate_cooldown_turns`) and a
**per-session cap** (`wall_gate_max_per_session`) keep it rare even on a long,
hard thread. It's **on by default and rare by construction**: the pre-gate only
decides *whether* to spend the deliberation; the conservative `at_wall()` logic
that decides *whether to actually surface the question* is unchanged, still asks
about **her** reasoning (never smuggles a fact), and still never auto-promotes.

**Beliefs grow across threads.** Deliberation is no longer write-only. Every
surviving synthesis is promoted into a dedicated **earned-belief layer**
(`BeliefMemory`, L2b) that is injected into the system prompt of *every future
thread* — right alongside (but strictly separate from) the user-stated persona
layer. A later thread that re-derives an equivalent belief **reinforces** it
(token-overlap match, not embeddings — distinct beliefs are never silently
merged); the layer is capped and **evicts the weakest first** (uncontested,
least-reinforced), so what persists is what kept earning its place. The standing
objection travels with each belief, so the next session sees the live tension
rather than a flattened conclusion. This is the mechanism by which the context
map *accumulates* deliberated understanding over time — while the append-only
ledger remains the full audit trail.

**Beliefs manage their own signal (conflict + autonomous pruning).** The belief
layer runs a small, deterministic **signal calculus** so it self-curates without
ever silently losing anything:

- **Signal score.** Each belief carries a live score that *grows* with how often
  it was independently re-earned and how much information it holds (a belief that
  survived a real objection outranks a bland consensus), and *decays* with how
  long it has gone untouched and how often it lost a conflict. Pure arithmetic on
  stored fields — no model, fully auditable.
- **Conflict resolution.** If a new belief *contradicts* an active one (same
  subject, opposite polarity — detected lexically, negation-aware, so a
  contradiction is never mistaken for agreement), the two are settled by the
  **same deliberation** that earns beliefs in the first place. The winner stays;
  the loser is **archived**, not deleted.
- **Quarantine, not deletion.** Beliefs whose signal falls below a floor — and
  cap-evictions — are moved to a retained **archive** tier (no longer injected,
  but auditable). If a quarantined belief is **re-earned** later, it **revives**
  with its history intact. Nothing the model would still hold is ever silently
  destroyed — which is what makes the self-pruning safe and non-regressive.

- **Salience weighting.** Each model-owned belief also carries a **salience**
  weight with sensible per-kind defaults (a stated value or commitment outranks a
  passing insight). Salience feeds the same signal score and a **keyword-boosted
  retrieval** path, so when context is rebuilt for a new thread the most relevant
  earned beliefs surface first instead of a flat recency dump.

> **Scope (the one invariant for all of the above):** every mechanism here —
> signal score, conflict, quarantine, salience — applies **only to the model's
> own beliefs**. User-stated facts and corrections bypass it entirely, stay
> verbatim, and are **never salience-weighted, decayed, or quarantined**. The
> user owns truth.

**Honest scope (no overclaim):** this is *contradiction-driven belief revision*,
not "self-awareness," and there is no literal fractal geometry — those are
inspiration, not mechanism. The scope invariant above is **enforced**, not just
intended: a deterministic doubt-scope guard keeps any insight that asserts — or
resembles — a user-anchored fact (name, location, job, preference, behavior
directive) out of the deliberation machine, on both the live and end-of-session
paths. Doubt is welcome
but it must be *real*: the model may challenge its own reasoning, never
manufacture doubt about something the user is the authority on (no more
"uncertain whether the user lives in Mebane"). Live deliberation is off the reply path entirely; the
end-of-session pass adds a few model calls (more only when disagreement is real).
Disable per-turn work with `live_deliberation_enabled: false` or all of it with
`deliberation_enabled: false` in [`config.yaml`](config.yaml). Any error fails
safe — the raw insight passes through unchanged.

## `[REMEMBER]` self-annotation (opt-in)

Normally an insight only becomes a durable belief at end-of-session (or via the
background live pass). With this opt-in feature on, the model can also mark a
mid-response insight worth keeping *as it writes*, with an inline
`[REMEMBER kind=... subject="..."]` tag:

- The tag is **stripped from what you see** — the reply reads normally.
- The insight is **persisted immediately**, so an abrupt exit can't lose it.
- It is routed through the **same doubt-scope guard** as everything else, so a tag
  that tries to assert a user-anchored fact (name, location, job, preference) is
  **rejected, not stored** — the model can capture its *own* reasoning, never
  manufacture a "fact" about you.

It's **off by default**. Turn it on with `live_annotation_enabled: true` in
[`config.yaml`](config.yaml). Any error fails safe — the reply passes through
unchanged and nothing is written.

## Self-tuning (RDST)

Tuning is an explicit, gated step — it never runs on its own.

```bash
./.venv/bin/python seedling.py tune                    # show the scoring table only
./.venv/bin/python seedling.py tune --approve-tuning   # build data + run LoRA update
```

Self-tuning runs on Apple's [MLX](https://github.com/ml-explore/mlx-lm) and is therefore **Apple-Silicon-only**; every other subsystem is cross-platform (see [Platform support](#platform-support)). Before a real run you'll need MLX and an MLX-converted model (LoRA can't tune a raw GGUF):

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
├── seedling.py                 # CLI entry point (chat/status/eval/bench/tune/...)
├── schemas.py                  # all dataclasses (state, deltas, critic, beliefs, tuning)
├── mcm.py                      # Mutable Context Map: restore / write / pause
├── session.py                  # ThreadSession: start / chat / end + transcripts
├── deliberation.py             # 3-voice adaptive-depth deliberation (end-of-session)
├── live_deliberation.py        # per-turn background deliberation (off the reply path)
├── wall.py                     # collaborative wall: fuzzy at_wall() surfacing decision
├── wallgate.py                 # model-free difficulty pre-gate (whether to spend a wall)
├── collaborate.py              # user co-authoring of a stalled belief
├── caution.py                  # forward-acting, downward-only caution controller
├── consolidation.py            # L3: fold gated deltas into cognitive_style + priors
├── critic.py                   # CriticInstance: local or Perplexity backend
├── voicelayer.py / voice.py    # local neural TTS + deterministic speak floor
├── filereader.py               # :read/:more file attach + chunking (txt/py/csv)
├── tuner.py                    # RDST: scoring, training-data build, LoRA tuning
├── storage.py                  # LanceDB wrapper (tables, snapshots)
├── eval.py                     # metrics, drift, failure-mode tests
├── eval_confabulation.py       # confabulation / persistence eval harness
├── smoke_test.py               # end-to-end smoke test (live, isolated DB)
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

**Self-shaping cognition (L3).** Above persona and beliefs sits a third tier:
the `cognitive_style` (abstraction level, dominant frameworks, contradiction
tolerance, how uncertainty is expressed) and `persistent_priors` (topic
salience, trust calibration, self-model confidence) that are injected into
*every* prompt. Each session's delta is folded into this tier by
`consolidation.py` so the model's reasoning posture evolves from what actually
survives critique. The fold is deliberately conservative: an **EMA** (old
signal decays, never deleted — non-regressive), **gated** so quarantined or
low-coherence deltas can't reshape cognition, and **deterministic** — every
field move is a printable function of the deltas, no model call, so you can
always answer *why* a value changed. *Honest scope:* an A/B eval
([results](docs/design/l3-eval-results.md)) shows L3 **measurably and
consistently** shifts reasoning posture toward the established style (0→67
framework invocations across 8 probes, qwen3:30b) **with no honesty
regression**. Whether the L3-shaped answers are *better* in a blind quality
judgment (not merely more on-style) is prepared but not yet run — treat that
as open, not proven.

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
