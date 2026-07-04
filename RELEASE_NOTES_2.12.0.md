<!-- release-title: v2.12.0 — Aida goes cross-platform (macOS, Linux, Windows) -->
**TL;DR:** Aida is a fully local AI you can actually trust: she **won't make things up** — integrity guards cut a small model's confabulation from **20% to 0%** in a reproducible eval — and she carries a **memory that's yours alone**: versioned, auditable, and correctable in plain language, never shipped to a cloud. As of **v2.12.0** that entire honesty-and-memory core, and her neural voice, run on **macOS, Linux, and Windows** — the same Aida everywhere, nothing dropped, and nothing changed for existing Apple Silicon users.

## Why install her

Most local LLM setups are amnesiacs that also confidently make things up. Aida is different on **both** counts — and no mainstream cloud assistant ships those two properties together:

- **She won't fabricate.** Capability and identity guards took a small (3B) model from **20% confabulation to 0%** across a 5-run adversarial battery. The guards, not model scale, do the work.
- **The memory is yours.** She stores *reasoning state* — preferences, frameworks, confidence — not a chat log, and it lives on your machine. You audit it, correct it in plain language, and it is never silently rewritten by a cloud.
- **It runs where you do, fully offline.** No cloud calls, no telemetry — now on Mac, Linux, or Windows.

## What's new in 2.12.0 — cross-platform

- **The core is now cross-platform.** Memory (MCM/LanceDB), self-critique, 3-voice deliberation, the caution controller, the earned-belief layer, `:read` file attachment, and every capability/identity guard run on macOS, Linux, and Windows. The full offline test suite is green on Linux.
- **Her voice crossed over too.** The Kokoro neural voice (`af_kore`) is portable (`kokoro-onnx` + `soundfile`); playback now picks an OS-appropriate player — `afplay` on macOS, `paplay`/`aplay`/`ffplay`/`play` on Linux, and the standard-library `winsound` on Windows.
- **Zero change for Apple Silicon.** The macOS path is byte-for-byte what it was: `afplay` is still tried first and the built-in `say` stays the Mac fallback. Every new branch only runs where the Mac binaries are absent.
- **One honest exception.** Gated self-tuning (RDST/LoRA) runs on Apple's MLX and remains Apple-Silicon-only. It is optional, explicitly approved, and never on the reply path — so elsewhere it is a missing extra, not a regression.

## Everything she does

- **Persistent memory across sessions** — a Mutable Context Map in LanceDB, restored into the prompt at start and updated with a structured delta at end.
- **Teach and correct in plain language** — "your name is Aida" or "that's wrong, the location is Mebane" promotes or deterministically corrects a fact; the model never guesses what to delete.
- **Self-critique** — a second model pass scores every reply for coherence, contradiction, and drift before it is logged.
- **Graded caution** — when her own coherence slips, a downward-only controller raises restraint on the *next* reply, with no gauge writes and no reply-path model call.
- **Earned beliefs + collaborative wall** — insights survive a thesis/antithesis/synthesis deliberation before persisting; on genuinely hard turns she asks you to co-author, gated to stay rare.
- **Read your files** — `:read` attaches a local text/Python/CSV file and pages large files in chunks with `:more`, verified at **0% confabulation**.
- **A real offline voice** — short, safe conversational replies spoken by a local neural voice; never code, numbers, paths, or file contents.
- **Recoverable and auditable** — every state write is logged; all state rebuilds from versioned snapshots.

## Install her

**Requirements:** Python 3.11–3.13 and [Ollama](https://ollama.com). macOS on Apple Silicon is the primary, best-tested target; Linux and Windows work too (on Windows, run the shell scripts under WSL or Git Bash).

```bash
git clone https://github.com/StewAlexander-com/continuous-ai.git
cd continuous-ai
bash setup.sh        # one-time: builds a venv, installs deps, pulls the model
bash setup_voice.sh  # optional, one-time: downloads Aida's local neural voice (~330 MB)
bash run.sh          # starts Ollama if needed, then drops you into chat
```

Install Ollama for your platform:

- **macOS:** `brew install ollama`
- **Linux:** `curl -fsSL https://ollama.com/install.sh | sh`
- **Windows:** download from [ollama.com/download](https://ollama.com/download), then run the scripts in WSL or Git Bash

`run.sh` is the single entry point — it starts the Ollama server if needed, ensures the model is pulled, and launches the chat loop. On macOS you can also just double-click `Seedling.command`.

## Tests

- Full offline suite **33/33 green** — adds cross-platform playback coverage (player selection, no-leak temp-file cleanup, and a safe no-op when no player or model is present).
- `compileall` + `schemas.py` + `eval.py` clean. Confabulation battery unchanged at **0%**.

**Full changes:** `v2.11.1..v2.12.0`
