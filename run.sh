#!/usr/bin/env bash
# Seedling one-button launcher.
# Usage:  bash run.sh            -> start Ollama if needed, then chat
#         bash run.sh status     -> show MCM state
#         bash run.sh eval        -> evaluation report
#         bash run.sh snapshot    -> manual snapshot
#         bash run.sh fresh       -> chat with no prior context
# Works from fish/zsh/bash since it runs under bash explicitly.

set -u
cd "$(dirname "$0")"

PY="./.venv/bin/python"
MODEL="llama3.2"
OLLAMA_URL="http://127.0.0.1:11434"

say() { printf "\033[1;36m==>\033[0m %s\n" "$1"; }
err() { printf "\033[1;31m!!\033[0m %s\n" "$1" >&2; }

# 0) venv sanity
if [ ! -x "$PY" ]; then
  err "No venv at $PY. Run:  bash setup.sh"
  exit 1
fi

# 1) Ensure Ollama is running
if ! curl -sf "$OLLAMA_URL/api/tags" >/dev/null 2>&1; then
  if ! command -v ollama >/dev/null 2>&1; then
    err "ollama not installed. Install with:  brew install ollama"
    exit 1
  fi
  say "Starting Ollama server in the background..."
  # Log to a file; detach so it survives this script.
  nohup ollama serve >/tmp/seedling_ollama.log 2>&1 &
  # Wait up to 30s for it to answer.
  for i in $(seq 1 30); do
    if curl -sf "$OLLAMA_URL/api/tags" >/dev/null 2>&1; then break; fi
    sleep 1
  done
  if ! curl -sf "$OLLAMA_URL/api/tags" >/dev/null 2>&1; then
    err "Ollama did not start within 30s. See /tmp/seedling_ollama.log"
    exit 1
  fi
  say "Ollama is up."
else
  say "Ollama already running."
fi

# 2) Ensure the model is available
if ! ollama list 2>/dev/null | grep -q "$MODEL"; then
  say "Model '$MODEL' not found locally — pulling (one-time, ~2GB)..."
  ollama pull "$MODEL"
fi

# 3) Dispatch
CMD="${1:-chat}"
case "$CMD" in
  chat)     say "Launching chat. Type one line per turn; type 'exit' to end."; exec "$PY" seedling.py chat ;;
  fresh)    say "Launching FRESH chat (no prior context)."; exec "$PY" seedling.py chat --fresh ;;
  status)   exec "$PY" seedling.py status ;;
  eval)     exec "$PY" seedling.py eval ;;
  snapshot) exec "$PY" seedling.py snapshot ;;
  *)        exec "$PY" seedling.py "$CMD" ;;
esac
