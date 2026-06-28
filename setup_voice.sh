#!/usr/bin/env bash
# setup_voice.sh — one-time, fully-local provisioning of Aida's neural voice (Kokoro).
#
# Aida's voice (af_kore) runs entirely on your machine via kokoro-onnx. The ~330 MB
# model files are NOT in git (they exceed GitHub's 100 MB limit), so this script
# downloads them once. Re-running is safe: present, correctly-sized files are skipped.
# Until the model is present, Aida transparently falls back to the macOS `say` voice.
set -euo pipefail

cd "$(dirname "$0")"

MODEL_URL="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx"
VOICES_URL="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin"
MODEL_FILE="kokoro-v1.0.onnx"
VOICES_FILE="voices-v1.0.bin"
MIN_MODEL_BYTES=$((300 * 1024 * 1024))   # sanity floor (~330 MB real)
MIN_VOICES_BYTES=$((10 * 1024 * 1024))   # sanity floor (~26 MB real)

PY="./.venv/bin/python"; [ -x "$PY" ] || PY="python3"

file_ok() {  # path  min_bytes  ->  0 if present and large enough
  local f="$1" min="$2"
  [ -f "$f" ] || return 1
  local sz
  sz=$(wc -c < "$f" | tr -d ' ')
  [ "$sz" -ge "$min" ]
}

fetch() {  # url  dest
  echo "  downloading $(basename "$2") ..."
  curl -fL --retry 3 -o "$2.partial" "$1"
  mv "$2.partial" "$2"
}

echo "== Aida voice setup (Kokoro, fully local) =="

# 1) Python deps (idempotent).
echo "-- ensuring python deps (kokoro-onnx, soundfile) --"
"$PY" -m pip install --quiet --upgrade kokoro-onnx soundfile

# 2) Model files (skip if already good).
if file_ok "$MODEL_FILE" "$MIN_MODEL_BYTES"; then
  echo "-- $MODEL_FILE present, skipping --"
else
  fetch "$MODEL_URL" "$MODEL_FILE"
fi
if file_ok "$VOICES_FILE" "$MIN_VOICES_BYTES"; then
  echo "-- $VOICES_FILE present, skipping --"
else
  fetch "$VOICES_URL" "$VOICES_FILE"
fi

# 3) Verify it loads + can synthesize (no audio device needed).
echo "-- verifying voice --"
"$PY" - <<'PYEOF'
import sys
try:
    import voicelayer as v
    ok = v.kokoro_available("kokoro-v1.0.onnx", "voices-v1.0.bin")
except Exception as e:
    print(f"  [warn] could not import voicelayer cleanly: {e}")
    ok = False
print("  kokoro_available:", ok)
sys.exit(0 if ok else 1)
PYEOF

echo "== Done. Aida will speak with af_kore (set tts_engine: kokoro in config.yaml). =="
echo "   If the model is ever missing, she falls back to the macOS 'say' voice automatically."
