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

echo "==> Installing dependencies"
pip install "ollama>=0.3.0" "lancedb>=0.6.0" "pyarrow>=15.0.0" "pyyaml>=6.0" "httpx>=0.27.0"

echo "==> Verifying Ollama server is up"
if ! curl -sf http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
  echo "!! Ollama not reachable on :11434. Start it in another terminal:  ollama serve"
  echo "   (or install the menubar app: brew install --cask ollama, then launch Ollama.app)"
else
  echo "    Ollama is up."
fi

echo "==> Pulling the model from config.yaml (llama3.2)"
ollama pull llama3.2 || echo "   (pull skipped/failed — run 'ollama serve' first, then 'ollama pull llama3.2')"

echo ""
echo "==> Setup complete. Next:"
echo "    source .venv/bin/activate"
echo "    python seedling.py status     # sanity check"
echo "    python seedling.py chat       # start a continuity-enabled session"
