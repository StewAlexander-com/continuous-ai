# Aida practical guide

[← Back to the README](../README.md)

Aida is the assistant; Continuous-AI manages the conversation and memory around a local language model. The interface is a terminal, not a hosted chat website. macOS / Apple Silicon is the primary development target. Core features also target Linux and Windows; use WSL or Git Bash for the shell scripts on Windows.

## Start with a smaller model

The standard `setup.sh` downloads the chat model in the tracked `config.yaml`, currently `qwen3:30b-a3b`. It does not read `config.local.yaml` when choosing that download. To avoid downloading the large default on your first run, install the dependencies manually and use an explicit model override instead.

Install Python 3.11–3.13 and Ollama, and start Ollama. From macOS or Linux, with Python 3.12 installed:

```bash
git clone https://github.com/StewAlexander-com/continuous-ai.git
cd continuous-ai
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
bash run.sh --model llama3.2
```

Use `python3.11` or `python3.13` in the environment-creation line if that is the supported interpreter you installed. The launcher downloads `llama3.2` if it is missing and applies the explicit override to chat and critic for that run. Keep using `bash run.sh --model llama3.2` for this smaller-model path; omitting the flag returns to the configured behavior. Smaller models can be less capable, and memory features do not remove that tradeoff.

The setup script deliberately selects Python 3.11–3.13. A newer Python version is not automatically supported by this script. Download size, available RAM, and response speed depend on the chosen model and hardware; this README does not establish a universal minimum-RAM figure.

### Shells and Windows

`bash run.sh` names the interpreter, so you can invoke it from zsh, fish, or bash without activating a virtual environment. On Windows, the documented script path requires WSL or Git Bash; WSL with a supported Python installed inside it offers the corresponding Unix-style paths. A Windows Python environment uses `.venv\Scripts\python.exe` instead of `.venv/bin/python` for direct commands. The Windows path was not executed during this documentation review.

For setup trouble, confirm that Ollama is running, that the model exists locally, and that the selected Python version is supported. `:setup` in a conversation reports backend and file-reader status.

## First conversation and saved memory

Try one request per line:

```text
Remember that I prefer short explanations with one example.
Explain what a local AI model is.
:help
exit
```

Start a second session and ask about your preference. The point is to check that context is available across sessions, not to assume every answer will obey it perfectly.

| Outside chat | Purpose |
| --- | --- |
| `bash run.sh` | Start a normal session |
| `bash run.sh --model llama3.2` | Use a smaller model for this run |
| `bash run.sh status` | Inspect saved state and configuration |
| `bash run.sh forget` | List persona facts without deleting them |
| `bash run.sh snapshot` | Save a manual state snapshot |
| `bash run.sh fresh` | Start without restoring prior context |

`fresh` is not a private or no-save mode. It changes how the session starts; it does not delete earlier storage or promise that the new conversation leaves no state behind.

To remove a persona fact, list facts first, then use `bash run.sh forget INDEX` with the index you intend to remove. Removing a current fact does not erase copies in old snapshots, logs, or backups. To correct instead, explain the mistake and replacement in the conversation. If the runtime cannot identify the intended fact unambiguously, it offers a numbered selection.

Aida keeps user-supplied facts separately from model-derived conclusions. In this design, a saved “belief” means a model-generated conclusion kept for later use, not a claim about consciousness. Those conclusions go through an objection/revision process and can retain dissent. `:why` / `:belief <id>` expose a stored summary of basis, limits, and conditions for reconsideration when those fields exist—not a hidden reasoning transcript. [Correction design](corrigibility.md).

## Files and optional features

- **Files:** `:read <path>` attaches a chosen file; `:more` pages a long attachment. Text, digital PDFs, and DOCX use the installed readers. Scanned PDFs need optional OCR tools. A large file is not necessarily supplied to the model in full.
- **Website content:** paste the text or save it as a local file. The normal chat runtime does not browse the web. Recognized requests to read a URL are intercepted by a code check; it does not catch every possible unsupported claim.
- **Voice:** `bash setup_voice.sh` installs optional local voice dependencies/assets. In chat, use `:voice` for status and `:voice off` to silence it. Installation can download files; playback is local once set up.
- **Search:** optional `ripgrep-all` search and scanning require installed tools, enabled flags, and allowed paths. See [contribution notes](../CONTRIBUTING.md#optional-corpus-search-rga). They are not required for a first conversation.
- **Model tuning:** optional LoRA training is an advanced Apple Silicon / MLX path requiring explicit approval and a compatible converted model. The post-training before/after evaluator is currently a stub; improved quality is not established. Ordinary saved memory does not require tuning.

## Settings and privacy

The tracked [config.yaml](../config.yaml) supplies defaults. `config.local.yaml` is a gitignored override file for personal runtime settings; commands such as `:theme`, `:enable`, and `:allow` write there. Nested mappings merge one level; lists replace rather than append. [Implementation](../localconfig.py).

The shell launchers still read `config.yaml` directly to decide which model to start or download. Keep that distinction in mind when changing runtime defaults in `config.local.yaml`; an explicit `--model` is the clearest per-run model choice.

The shipped configuration uses local inference and a local critic. The critic is a background review of the question and answer; its judgment can also be wrong. The configured chat model is `qwen3:30b-a3b`, and the separate local critic model is `gemma3:4b`. Local OpenAI-compatible servers are an alternative to Ollama; the supported inference adapter rejects remote endpoints.

**Cloud exception:** `critic_backend: perplexity` plus an API key sends the user query and model response to Perplexity. Material quoted from an attachment can therefore leave the machine through those messages. Keep `critic_backend: local` for the local review path. [Request construction](../critic.py).

Runtime data includes the local database, logs, snapshots, and optional ledgers. Git ignores those paths; that is not encryption or automatic deletion. Apply normal computer, account, and backup protections. See [storage.py](../storage.py) and [the ignore rules](../.gitignore) for the actual paths and persistence behavior.

## How the pieces fit

| Component | Plain-language role |
| --- | --- |
| [llm.py](../llm.py) | Connects to the local model that produces replies |
| [mcm.py](../mcm.py) and [storage.py](../storage.py) | Assemble and save context between sessions |
| [session.py](../session.py) | Coordinates the conversation and runtime checks |
| [critic.py](../critic.py) and [caution.py](../caution.py) | Review answers and adjust how cautiously later replies are framed |
| [deliberation.py](../deliberation.py) | Challenge model-derived conclusions before saving them |
| [corrigibility.py](../corrigibility.py) | Record correction information and conditions for reconsideration |

More depth: [memory layers](design/memory-layering.md), [caution design](design/caution-disposition.md), [voice and background work](design/voice-hybrid-deliberation.md), and [test evidence](readme-evidence.md).
