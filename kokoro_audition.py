#!/usr/bin/env python3
"""One-time audition: play several natural female Kokoro voices so you can pick.
Run in the seedling venv AFTER: pip install kokoro-onnx soundfile, and after the
kokoro-v1.0.onnx + voices-v1.0.bin files are downloaded into this directory.

Usage:  python3 kokoro_audition.py
Fully local. Writes temp wavs to /tmp and plays them with afplay.
"""
import os
import subprocess
import sys
import tempfile

try:
    import soundfile as sf
    from kokoro_onnx import Kokoro
except Exception as e:  # pragma: no cover - environment check
    print(f"[setup] missing deps: {e}\n  pip install kokoro-onnx soundfile")
    sys.exit(1)

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL = os.path.join(HERE, "kokoro-v1.0.onnx")
VOICES = os.path.join(HERE, "voices-v1.0.bin")
for p in (MODEL, VOICES):
    if not os.path.exists(p):
        print(f"[setup] missing model file: {p}")
        print("  download kokoro-v1.0.onnx and voices-v1.0.bin (see instructions)")
        sys.exit(1)

# Natural American-female candidates, ordered by typical clarity/naturalness.
CANDIDATES = ["af_heart", "af_bella", "af_nicole", "af_sarah", "af_aoede", "af_kore"]
LINE = ("Hi, I'm Aida. I speak short, plain replies out loud, "
        "and I stay silent on code and numbers.")

def main():
    print("[kokoro] loading model (one-time, ~5s)...")
    k = Kokoro(MODEL, VOICES)
    for name in CANDIDATES:
        try:
            print(f"\n=== {name} ===")
            samples, rate = k.create(LINE, voice=name, speed=1.0, lang="en-us")
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False, dir="/tmp") as f:
                wav = f.name
            sf.write(wav, samples, rate)
            subprocess.run(["afplay", wav], check=False)
            os.unlink(wav)
        except Exception as e:
            print(f"  [skip] {name}: {e}")
    print("\nDone. Tell me which voice name you liked best (e.g. af_heart).")

if __name__ == "__main__":
    main()
