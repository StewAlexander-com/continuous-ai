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
  echo "!! No Python 3.11–3.13 found. Your system Python is 3.14, which lacks lancedb/pyarrow wheels."
  echo "   Install one with:  brew install python@3.12"
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
  echo "   (or install the menubar app: brew install --cask ollama, then launch Ollama.app)"
else
  echo "    Ollama is up."
fi

# Read the model from config.yaml so setup pulls exactly what the runtime will
# use (single source of truth — same value run.sh reads). Avoids pulling a model
# the user doesn't need. Falls back to llama3.2 if config can't be read.
MODEL="$(python -c "import yaml; print((yaml.safe_load(open('config.yaml')) or {}).get('model_name','llama3.2'))" 2>/dev/null || echo llama3.2)"
echo "==> Pulling the model from config.yaml: $MODEL"
echo "    (7-14B models are ~4-9GB; this can take a few minutes on first install.)"
ollama pull "$MODEL" || echo "   (pull skipped/failed — start Ollama, then: ollama pull $MODEL)"

echo ""
echo "==> Setup complete. Next:"
echo "    bash run.sh                    # starts Ollama if needed, then chat"
echo "    bash run.sh status             # sanity check (model, DB, prior threads)"
echo "    bash run.sh smoke              # end-to-end smoke test (verifies the whole stack)"
echo ""
echo "    Or activate the venv directly:"
echo "    source .venv/bin/activate && python seedling.py chat"
