**TL;DR:** Aida leaves the Mac. The whole honesty-and-memory core — persistent memory, self-critique, deliberation, graded caution, earned beliefs, file reading, and every integrity guard — now runs on **macOS, Linux, and Windows**, and it is the *same* Aida on all three: no feature was dropped and nothing changed for existing Apple Silicon users. Her neural voice travels too: Kokoro synthesis was already portable, so this release teaches playback to speak on Linux and Windows as well. macOS on Apple Silicon remains the primary, best-tested target; the one genuinely Apple-only capability is optional LoRA self-tuning (it runs on Apple's MLX).

## Cross-platform, honestly

- **The core is now cross-platform.** Memory (MCM/LanceDB), self-critique, 3-voice deliberation, the caution controller, the earned-belief layer, `:read` file attachment, and all capability/identity guards run on macOS, Linux, and Windows. The full offline test suite is green on Linux.
- **Her voice crossed over too.** The Kokoro neural voice (`af_kore`) is portable (`kokoro-onnx` + `soundfile`); playback now picks an OS-appropriate player — `afplay` on macOS, `paplay`/`aplay`/`ffplay`/`play` on Linux, and the standard-library `winsound` on Windows.
- **Zero change for Apple Silicon.** The macOS path is byte-for-byte what it was: `afplay` is still tried first and the built-in `say` remains the Mac fallback. Every new branch only runs where the Mac binaries are absent.
- **One honest exception.** Gated self-tuning (RDST/LoRA) runs on Apple's MLX and stays Apple-Silicon-only. It is optional, explicitly approved, and never on the reply path — so on Linux/Windows it is a missing extra, not a regression.

## What Aida is (the key features)

- **She won't make things up.** Capability and identity guards cut a small model's confabulation from **20% to 0%** in a reproducible 5-run ablation — the guards, not model scale, do the work.
- **Memory you own.** A Mutable Context Map stores *reasoning state* (preferences, frameworks, confidence), not a chat log — versioned, auditable, and yours.
- **Teach and correct in plain language.** Say "your name is Aida" or "that's wrong, the location is Mebane" and durable facts are promoted or deterministically corrected — the model never guesses what to delete.
- **Self-critique.** A second model pass scores every reply for coherence, contradiction, and drift before it is logged.
- **Graded caution.** When her own coherence slips, a downward-only controller raises restraint on the *next* reply — no gauge writes, no reply-path model call, fully auditable.
- **Earned beliefs + collaborative wall.** Insights survive a thesis/antithesis/synthesis deliberation before persisting; on genuinely hard turns she pauses and asks you to co-author, gated to stay rare.
- **Read your files.** `:read` attaches a local text/Python/CSV file and pages large files in context-sized chunks with `:more`, verified at **0% confabulation**.
- **A real, offline voice.** Short, safe conversational replies spoken by a local neural voice — never code, numbers, paths, or file contents.
- **Offline by default.** No cloud calls, no telemetry; every state write is logged and recoverable from snapshots.

## Install her

**Requirements:** Python 3.11–3.13 and [Ollama](https://ollama.com). macOS on Apple Silicon is the primary target; Linux and Windows work too (on Windows, run the shell scripts under WSL or Git Bash).

```bash
git clone https://github.com/StewAlexander-com/continuous-ai.git
cd continuous-ai
bash setup.sh        # one-time: builds a venv, installs deps, pulls the model
bash setup_voice.sh  # optional, one-time: downloads Aida's local neural voice (~330 MB)
bash run.sh          # starts Ollama if needed, then drops you into chat
```

Install Ollama per platform:

- **macOS:** `brew install ollama`
- **Linux:** `curl -fsSL https://ollama.com/install.sh | sh`
- **Windows:** download from [ollama.com/download](https://ollama.com/download) (run the scripts in WSL or Git Bash)

`run.sh` is the single entry point — it starts the Ollama server if needed, ensures the model is pulled, and launches the chat loop. On macOS you can also just double-click `Seedling.command`.

## Tests

- Full offline suite **33/33 green** (adds cross-platform playback coverage: player selection, no-leak temp-file cleanup, and safe no-op when no player or model is present).
- `compileall` + `schemas.py` + `eval.py` clean. Confabulation battery unchanged at **0%**.

**Full changes:** `v2.11.1..v2.12.0`
