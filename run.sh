#!/usr/bin/env bash
# Seedling one-button launcher.
# Usage:  bash run.sh                         -> start Ollama if needed, then chat
#         bash run.sh status                  -> show MCM state
#         bash run.sh eval                    -> evaluation report
#         bash run.sh snapshot                -> manual snapshot
#         bash run.sh fresh                   -> chat with no prior context
#         bash run.sh --model qwen2.5:7b      -> chat with a one-off model (auto-pulls)
#         bash run.sh fresh --model llama3.1:8b
#
# The DEFAULT model is read from config.yaml (model_name) — single source of
# truth. --model overrides it for this run only (chat + critic), without editing
# config. Works from fish/zsh/bash since it runs under bash explicitly.

set -u
cd "$(dirname "$0")"

PY="./.venv/bin/python"
OLLAMA_URL="http://127.0.0.1:11434"

# --- Resolve the model: --model flag wins, else config.yaml, else fallback ---
MODEL_OVERRIDE=""
FWD_ARGS=()
for ((i=1; i<=$#; i++)); do
  a="${!i}"
  if [ "$a" = "--model" ]; then
    j=$((i+1)); MODEL_OVERRIDE="${!j:-}"; i=$j
  elif [[ "$a" == --model=* ]]; then
    MODEL_OVERRIDE="${a#--model=}"
  else
    FWD_ARGS+=("$a")
  fi
done

# Read model_name from config.yaml (single source of truth) via the venv python
# so we don't depend on a YAML CLI tool. Fallback to llama3.2 if anything fails.
CONFIG_MODEL="$("$PY" -c "import yaml,sys; print((yaml.safe_load(open('config.yaml')) or {}).get('model_name','llama3.2'))" 2>/dev/null || echo llama3.2)"
MODEL="${MODEL_OVERRIDE:-$CONFIG_MODEL}"

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

# 2) Ensure the model is available (auto-pull, with a clear size heads-up)
if ! ollama list 2>/dev/null | grep -q "$MODEL"; then
  say "Model '$MODEL' not found locally — pulling now (one-time download)."
  say "    7-14B models are ~4-9GB; this can take a few minutes."
  ollama pull "$MODEL" || { err "Pull failed for '$MODEL'. Check the name with: ollama list"; exit 1; }
fi

if [ -n "$MODEL_OVERRIDE" ]; then
  say "Using model override: $MODEL (chat + critic, this run only)"
else
  say "Using model: $MODEL (from config.yaml)"
fi

# 3) Dispatch
# Pass --model through so seedling.py overrides chat + critic consistently.
# NOTE: no `exec` — we want control to return to the caller (e.g. the
# Seedling.command launcher) so it can keep the window open afterward.
MODEL_ARG=()
[ -n "$MODEL_OVERRIDE" ] && MODEL_ARG=(--model "$MODEL_OVERRIDE")
# Safe array expansion under `set -u` on macOS bash 3.2: the ${arr[@]+"..."}
# form expands to NOTHING when the array is empty, instead of erroring.
MA=(${MODEL_ARG[@]+"${MODEL_ARG[@]}"})
FA=(${FWD_ARGS[@]+"${FWD_ARGS[@]}"})
CMD="${FA[0]:-chat}"
case "$CMD" in
  chat)     say "Launching chat. Type one line per turn; type 'exit' to end."; "$PY" seedling.py chat ${MA[@]+"${MA[@]}"} ;;
  fresh)    say "Launching FRESH chat (no prior context)."; "$PY" seedling.py chat --fresh ${MA[@]+"${MA[@]}"} ;;
  status)   "$PY" seedling.py status ${MA[@]+"${MA[@]}"} ;;
  eval)     "$PY" seedling.py eval ${MA[@]+"${MA[@]}"} ;;
  snapshot) "$PY" seedling.py snapshot ${MA[@]+"${MA[@]}"} ;;
  *)        "$PY" seedling.py ${FA[@]+"${FA[@]}"} ${MA[@]+"${MA[@]}"} ;;   # forward all args (e.g. 'forget 1')
esac
