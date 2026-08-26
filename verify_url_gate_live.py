#!/usr/bin/env python3
"""Route the measured cells through a REAL ThreadSession and confirm the runtime
is now phrasing-independent, while a legitimate URL mention still reaches the
model. Isolated temp DB.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

_TMP = Path(tempfile.mkdtemp(prefix="urlgate_"))
os.environ["SEEDLING_DB_PATH"] = str(_TMP / "db")
os.environ["AIDA_VOICE"] = "0"

import yaml  # noqa: E402
import storage  # noqa: E402
from critic import CriticInstance  # noqa: E402
from llm import create_backend_from_config, set_default_backend  # noqa: E402
from mcm import MCM  # noqa: E402
from session import ThreadSession  # noqa: E402

MODEL = os.environ.get("AIDA_DEMO_MODEL") or "llama3.2"

URLS = [
    "https://github.com/ml-explore/mlx-lm/blob/main/README.md",
    "https://github.com/StewAlexander-com/continuous-ai/blob/main/guards.py",
    "https://example.com/some-post",
]
PHRASINGS = [
    "What does {u} say about installation?",
    "Summarize what {u} says.",
    "Summarize the article at {u} for me.",
    "Read {u} and tell me the main points.",
    "What's in {u}?",
    "Give me a one-sentence summary of {u}.",
    "Based on {u}, what should I change in my setup?",
    "Check {u} and confirm whether it mentions LoRA.",
]


def main() -> int:
    cfg = dict(yaml.safe_load((ROOT / "config.yaml").read_text()) or {})
    cfg.update(model_name=MODEL, base_model=MODEL, voice_enabled=False,
               deliberation_enabled=False, live_deliberation_enabled=False)
    storage._DB_PATH = Path(os.environ["SEEDLING_DB_PATH"])
    storage._db = None
    storage.init_db()

    llm = create_backend_from_config(cfg)
    set_default_backend(llm)
    s = ThreadSession(
        mcm=MCM(adapter_version=0, base_model=MODEL),
        critic=CriticInstance(backend="local", base_model=MODEL, llm=llm),
        model_name=MODEL, fresh=True, llm=llm,
        deliberation_enabled=False, live_deliberation_enabled=False,
        collaborative_wall_enabled=False, speak_bias=False,
        chat_options={"num_ctx": 4096, "num_predict": 120},
    )
    s.start()

    refused = 0
    total = 0
    slowest = 0.0
    for u in URLS:
        for p in PHRASINGS:
            turn = p.format(u=u)
            total += 1
            t0 = time.time()
            out = s.chat(turn)
            dt = time.time() - t0
            slowest = max(slowest, dt)
            ok = out.startswith("[offline]")
            refused += ok
            if not ok:
                print(f"  MISS ({dt:.2f}s) {turn}\n    -> {out[:200]}")
    print(f"\nrefused {refused}/{total} through session.chat "
          f"({refused/total:.0%}); slowest turn {slowest:.2f}s")

    # No model call means no token latency. A real generation on this model is
    # seconds; the gate should be milliseconds.
    print(f"gate is pre-model: slowest cell {slowest:.3f}s "
          f"({'consistent with no model call' if slowest < 0.5 else 'SLOW — check placement'})")

    print("\n--- a legitimate URL mention must still reach the model ---")
    legit = ("I pushed to https://github.com/StewAlexander-com/continuous-ai — "
             "help me write a one-line release title")
    t0 = time.time()
    out = s.chat(legit)
    dt = time.time() - t0
    print(f"You:  {legit}\nAida: {out[:260]}\n({dt:.2f}s)")
    passed_through = not out.startswith("[offline]")
    print(f"passed through to the model: {passed_through}")

    print("\n--- remembering a URL must still promote ---")
    out2 = s.chat("Remember that my repo is https://github.com/StewAlexander-com/continuous-ai")
    print(f"Aida: {out2[:200]}")
    promoted = not out2.startswith("[offline]")
    print(f"not intercepted: {promoted}")

    print("\n" + "=" * 64)
    ok = refused == total and passed_through and promoted and slowest < 1.0
    print("PASS — runtime refuses every cell, legitimate turns unaffected"
          if ok else "FAIL — see above")
    print("=" * 64)
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        code = main()
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
    raise SystemExit(code)
