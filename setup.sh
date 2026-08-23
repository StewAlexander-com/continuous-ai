#!/usr/bin/env bash
# Seedling setup — run this in your normal Terminal (NOT via any sandbox).
# It creates a venv, installs deps, and verifies Ollama is reachable.
set -euo pipefail
cd "$(dirname "$0")"

echo "==> Choosing a Python interpreter (need 3.11–3.13; avoid 3.14 — no lancedb/pyarrow wheels yet)"
PYBIN=""
for cand in python3.13 python3.12 python3.11; do
  if command -v "$cand" >/dev/null 2>&1; then PYBIN="$cand"; break; fi
done
if [ -z "$PYBIN" ]; then
  echo "!! No Python 3.11–3.13 found (LanceDB's 3.14 wheels are still inconsistent)."
  case "$(uname -s)" in
    Darwin) echo "   Install one with:  brew install python@3.12" ;;
    Linux)  echo "   Install one with:  sudo apt install python3.12 python3.12-venv   (or your distro's package)" ;;
    *)      echo "   Install a Python 3.11–3.13 build from https://www.python.org/downloads/" ;;
  esac
  echo "   Then re-run this script."
  exit 1
fi
echo "    Using: $($PYBIN --version) at $(command -v $PYBIN)"

echo "==> Creating .venv"
"$PYBIN" -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip

echo "==> Installing dependencies (from requirements.txt — single source of truth)"
if [ -f requirements.txt ]; then
  pip install -r requirements.txt
else
  # Fallback if requirements.txt is missing for any reason.
  pip install "ollama>=0.3.0" "lancedb>=0.6.0" "pyarrow>=15.0.0" "pyyaml>=6.0" "httpx>=0.27.0"
fi

echo "==> Verifying Ollama server is up"
if ! curl -sf http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
  echo "!! Ollama not reachable on :11434. Start it in another terminal:  ollama serve"
  case "$(uname -s)" in
    Darwin) echo "   (or install the menubar app: brew install --cask ollama, then launch Ollama.app)" ;;
    Linux)  echo "   (or install it: curl -fsSL https://ollama.com/install.sh | sh)" ;;
    *)      echo "   (or install it from https://ollama.com/download)" ;;
  esac
else
  echo "    Ollama is up."
fi

# Read the model from config.yaml so setup pulls exactly what the runtime will
# use (single source of truth — same value run.sh reads). Avoids pulling a model
# the user doesn't need. Falls back to llama3.2 if config can't be read.
MODEL="$(python -c "import yaml; print((yaml.safe_load(open('config.yaml')) or {}).get('model_name','llama3.2'))" 2>/dev/null || echo llama3.2)"
INFERENCE_BACKEND="$(python -c "import yaml; print((yaml.safe_load(open('config.yaml')) or {}).get('inference_backend','ollama'))" 2>/dev/null || echo ollama)"
echo "==> Pulling the model from config.yaml: $MODEL"
if [ "$INFERENCE_BACKEND" = "openai_compat" ]; then
  echo "    (inference_backend is openai_compat — skip Ollama pull; load the model in your server UI)"
else
  echo "    (7-14B models are ~4-9GB; this can take a few minutes on first install.)"
  ollama pull "$MODEL" || echo "   (pull skipped/failed — start Ollama, then: ollama pull $MODEL)"
fi

if command -v rga >/dev/null 2>&1; then
  echo "==> Optional rga (ripgrep-all) found: $(command -v rga)"
else
  echo "==> Optional rga not found. Corpus search (:search) stays off until you install it"
  echo "    (e.g. brew install ripgrep-all) and set rga_search_enabled in config.yaml."
fi

# A venv ships an activate script per shell family, and naming the wrong one is
# a syntax error rather than a graceful failure: fish reading the POSIX
# `activate` stops at `_OLD_VIRTUAL_PATH="$PATH"` with "Unsupported use of '='".
# So print the line that matches the shell this was launched from. $SHELL is the
# *login* shell and is routinely wrong for someone trying another one, so ask
# the parent process what actually invoked us and keep $SHELL as the fallback.
# macOS reports login shells with a leading dash, hence trimming it.
launching_shell() {
  local p=""
  p="$(ps -p "${PPID:-0}" -o comm= 2>/dev/null | tr -d ' -' || true)"
  case "$p" in
    fish|zsh|bash|ksh|dash|sh|csh|tcsh) printf '%s' "$p" ;;
    *)                                  basename "${SHELL:-sh}" ;;
  esac
}
case "$(launching_shell)" in
  fish)     ACTIVATE="source .venv/bin/activate.fish" ;;
  csh|tcsh) ACTIVATE="source .venv/bin/activate.csh" ;;
  *)        ACTIVATE="source .venv/bin/activate" ;;
esac

echo ""
echo "==> Setup complete. Next:"
echo "    bash run.sh                    # starts Ollama if needed, then chat"
echo "    bash run.sh status             # sanity check (model, DB, prior threads)"
echo "    bash run.sh smoke              # end-to-end smoke test (verifies the whole stack)"
echo ""
echo "    That works the same from fish, zsh, bash or sh — the script names bash"
echo "    itself, so your own shell never has to be one. './run.sh' works too."
echo ""
echo "    Or activate the venv directly:"
echo "    $ACTIVATE && python seedling.py chat"
