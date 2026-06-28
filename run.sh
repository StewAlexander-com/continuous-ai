#!/usr/bin/env bash
# Seedling one-button launcher.
# Usage:  bash run.sh                         -> start Ollama if needed, then chat
#         bash run.sh status                  -> show MCM state
#         bash run.sh eval                    -> evaluation report
#         bash run.sh confab-eval             -> confabulation/persistence eval (live)
#         bash run.sh smoke                   -> end-to-end smoke test (live, isolated DB)
#         bash run.sh health                  -> full health check: parse + tests + honesty + smoke (PASS/FAIL)
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

# Determine the subcommand early. The `health` command skips this HARD Ollama
# preamble (which exits on failure) because it must still run its STATIC checks
# offline and SKIP the live checks cleanly when Ollama is down — it does its own
# SOFT Ollama detection inside the dispatch below.
CMD0="${FWD_ARGS[0]:-chat}"
if [ "$CMD0" != "health" ]; then
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
  confab-eval)
            say "Running confabulation / persistence eval (live model: $MODEL)..."
            MODEL_FLAG=()
            [ -n "$MODEL_OVERRIDE" ] && MODEL_FLAG=(--model "$MODEL_OVERRIDE")
            "$PY" eval_confabulation.py ${MODEL_FLAG[@]+"${MODEL_FLAG[@]}"} ;;
  smoke)    say "Running end-to-end smoke test against the live model (isolated temp DB)..."
            SMOKE_FLAG=()
            [ -n "$MODEL_OVERRIDE" ] && SMOKE_FLAG=(--model "$MODEL_OVERRIDE")
            "$PY" smoke_test.py ${SMOKE_FLAG[@]+"${SMOKE_FLAG[@]}"} ;;
  bench)    say "Benchmarking responsiveness (TTFT / tok-per-sec, isolated temp DB)..."
            "$PY" seedling.py bench ${FA[@]+"${FA[@]}"} ${MA[@]+"${MA[@]}"} ;;
  health)
            # One-command health check: STATIC (offline) then LIVE (needs Ollama).
            # Static checks always run; live checks SKIP cleanly (exit 0) when
            # Ollama is unreachable. Any FAIL => exit 1. Honest scope: this proves
            # Aida is structurally sound + honest under test, NOT that the
            # cognitive layers' benefit is proven.
            say "Aida HEALTH CHECK — static (offline), then live (needs Ollama)."
            export PYTHONDONTWRITEBYTECODE=1   # never let .pyc perms cause false fails
            H_FAIL=0
            H_SUMMARY=""
            hc() {  # $1=PASS|FAIL|SKIP|INFO  $2=label
              H_SUMMARY="${H_SUMMARY}  [$1] $2
"
              [ "$1" = "FAIL" ] && H_FAIL=$((H_FAIL+1))
              return 0
            }

            # --- 1) STATIC: parse-gate every *.py ---
            say "[static] Parse-gating every *.py ..."
            PARSE_BAD=""; NPY=0
            for f in *.py; do
              [ -e "$f" ] || continue
              NPY=$((NPY+1))
              # Parse-only check that writes NOTHING (py_compile writes .pyc even with
              # PYTHONDONTWRITEBYTECODE and so false-fails in read-only/restricted dirs).
              "$PY" -c 'import ast,sys; ast.parse(open(sys.argv[1]).read())' "$f" >/dev/null 2>&1 || PARSE_BAD="$PARSE_BAD $f"
            done
            if [ -n "$PARSE_BAD" ]; then err "Parse failures:$PARSE_BAD"; hc FAIL "Parse gate ($NPY files)"
            else hc PASS "Parse gate ($NPY files)"; fi

            # --- schemas.py + eval.py run cleanly ---
            say "[static] Running schemas.py ..."
            if "$PY" schemas.py >/dev/null 2>&1; then hc PASS "schemas.py"; else err "schemas.py failed"; hc FAIL "schemas.py"; fi
            say "[static] Running eval.py ..."
            if "$PY" eval.py >/dev/null 2>&1; then hc PASS "eval.py"; else err "eval.py failed"; hc FAIL "eval.py"; fi

            # --- 1b) STATIC: full test suite (every test_*.py) ---
            say "[static] Running test suite (test_*.py) ..."
            T_PASS=0; T_TOTAL=0; T_BAD=""
            for t in test_*.py; do
              [ -e "$t" ] || continue
              T_TOTAL=$((T_TOTAL+1))
              if "$PY" "$t" >/dev/null 2>&1; then T_PASS=$((T_PASS+1)); else T_BAD="$T_BAD $t"; fi
            done
            if [ -n "$T_BAD" ]; then err "Failing suites:$T_BAD"; hc FAIL "Test suite ($T_PASS/$T_TOTAL suites)"
            else hc PASS "Test suite ($T_PASS/$T_TOTAL suites)"; fi

            # --- 2) LIVE: soft-detect Ollama; SKIP cleanly if unreachable ---
            LIVE_OK=0
            if curl -sf "$OLLAMA_URL/api/tags" >/dev/null 2>&1; then
              LIVE_OK=1; say "Ollama already running."
            elif command -v ollama >/dev/null 2>&1; then
              say "Starting Ollama server in the background..."
              nohup ollama serve >/tmp/seedling_ollama.log 2>&1 &
              for i in $(seq 1 30); do
                curl -sf "$OLLAMA_URL/api/tags" >/dev/null 2>&1 && break
                sleep 1
              done
              if curl -sf "$OLLAMA_URL/api/tags" >/dev/null 2>&1; then LIVE_OK=1; say "Ollama is up."; fi
            fi

            if [ "$LIVE_OK" -ne 1 ]; then
              err "Ollama not reachable — SKIPPING live checks. Start it with: ollama serve  (or install: brew install ollama)"
              hc SKIP "Honesty gate (confab 0%) — Ollama unreachable"
              hc SKIP "Smoke test — Ollama unreachable"
              hc SKIP "Responsiveness bench — Ollama unreachable"
            else
              # Auto-pull the model if missing (same as the other live commands).
              if ! ollama list 2>/dev/null | grep -q "$MODEL"; then
                say "Model '$MODEL' not found locally — pulling now (one-time download)."
                ollama pull "$MODEL" || err "Pull failed for '$MODEL'."
              fi
              HMODEL_FLAG=()
              [ -n "$MODEL_OVERRIDE" ] && HMODEL_FLAG=(--model "$MODEL_OVERRIDE")

              # Honesty gate: confabulation rate must be 0.0% / [GOOD].
              say "[live] Honesty gate: eval_confabulation.py (live model: $MODEL) ..."
              CONFAB_OUT="$("$PY" eval_confabulation.py ${HMODEL_FLAG[@]+"${HMODEL_FLAG[@]}"} 2>&1)"
              printf "%s\n" "$CONFAB_OUT"
              if printf "%s" "$CONFAB_OUT" | grep -Eq "Confabulation rate[^]]*(0\.0%|\[GOOD\])|\[GOOD\]"; then
                hc PASS "Honesty gate (0% confabulation)"
              else
                err "Confabulation gate not [GOOD] / 0.0% — see output above."
                hc FAIL "Honesty gate (confabulation > 0%)"
              fi

              # End-to-end smoke: PASS only if smoke_test.py exits 0 (all checks passed).
              say "[live] End-to-end smoke: smoke_test.py ..."
              if "$PY" smoke_test.py ${HMODEL_FLAG[@]+"${HMODEL_FLAG[@]}"}; then hc PASS "Smoke test"; else err "Smoke test reported failures."; hc FAIL "Smoke test"; fi

              # Responsiveness: informational only; never fails the run.
              say "[live] Responsiveness: seedling.py bench 3 (informational) ..."
              "$PY" seedling.py bench 3 ${HMODEL_FLAG[@]+"${HMODEL_FLAG[@]}"} || true
              hc INFO "Responsiveness bench (informational)"
            fi

            # --- FINAL HEALTH SUMMARY ---
            printf "\n"
            say "HEALTH SUMMARY"
            printf "%s" "$H_SUMMARY"
            H_NP="$(printf "%s" "$H_SUMMARY" | grep -c '\[PASS\]')"
            H_NT="$(printf "%s" "$H_SUMMARY" | grep -Ec '\[PASS\]|\[FAIL\]')"
            if [ "$H_FAIL" -eq 0 ]; then
              say "Aida is healthy — $H_NP/$H_NT checks passed (any SKIP = live checks not run because Ollama was down)."
              exit 0
            else
              err "UNHEALTHY — $H_FAIL check(s) failed; see failures above."
              exit 1
            fi ;;
  snapshot) "$PY" seedling.py snapshot ${MA[@]+"${MA[@]}"} ;;
  *)        "$PY" seedling.py ${FA[@]+"${FA[@]}"} ${MA[@]+"${MA[@]}"} ;;   # forward all args (e.g. 'forget 1')
esac
