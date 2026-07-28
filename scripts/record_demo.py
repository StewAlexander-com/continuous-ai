#!/usr/bin/env python3
"""Record a REAL Aida session for docs/assets/demo.gif (isolated temp DB).

Drives the same ThreadSession path as chat, against a throwaway database, so
the GIF is a live capture — not an AI-illustrated recreation. Used by
docs/assets/demo.tape via VHS:

    vhs docs/assets/demo.tape

Or run standalone to preview:

    AIDA_VOICE=0 ./.venv/bin/python scripts/record_demo.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

# Isolate BEFORE importing storage/session so the real .seedling_db is untouched.
_TMP = Path(tempfile.mkdtemp(prefix="aida_demo_"))
os.environ["SEEDLING_DB_PATH"] = str(_TMP / "db")
os.environ["AIDA_VOICE"] = "0"

import yaml  # noqa: E402
import storage  # noqa: E402
from critic import CriticInstance  # noqa: E402
from llm import create_backend_from_config, set_default_backend  # noqa: E402
from mcm import MCM  # noqa: E402
from session import ThreadSession  # noqa: E402
import ui  # noqa: E402


def _pause(s: float = 0.55) -> None:
    time.sleep(s)


def _say(line: str = "") -> None:
    print(line, flush=True)
    _pause(0.25)


def _type_user(text: str) -> None:
    """Print a You: line the way the CLI does (no fake typing animation needed;
    VHS captures the real turn)."""
    print(f"You: {text}", flush=True)
    _pause(0.4)


def _build_session(config: dict, *, fresh: bool, model: str) -> ThreadSession:
    llm = create_backend_from_config(config)
    set_default_backend(llm)
    mcm = MCM(
        adapter_version=config.get("adapter_version", 0),
        base_model=config.get("base_model", model),
    )
    critic = CriticInstance(
        backend=config.get("critic_backend", "local"),
        base_model=config.get("base_model", model),
        llm=llm,
    )
    return ThreadSession(
        mcm=mcm,
        critic=critic,
        model_name=model,
        fresh=fresh,
        llm=llm,
        deliberation_enabled=False,
        live_deliberation_enabled=False,
        collaborative_wall_enabled=False,
        speak_bias=False,
        caution_controller_enabled=config.get("caution_controller_enabled", True),
        chat_options=config.get("chat_options") or {"num_ctx": 4096, "num_predict": 120},
    )


def _stream_reply(session: ThreadSession, prompt: str) -> str:
    started = False

    def on_token(tok: str) -> None:
        nonlocal started
        if not started:
            sys.stdout.write("Aida: ")
            started = True
        sys.stdout.write(tok)
        sys.stdout.flush()

    text = session.chat(prompt, on_token=on_token)
    if not started:
        # Non-stream / memory short-circuit path
        print(f"Aida: {text}", flush=True)
    else:
        print(flush=True)
    return text


def main() -> int:
    cfg_path = ROOT / "config.yaml"
    config = yaml.safe_load(cfg_path.read_text()) if cfg_path.exists() else {}
    # Prefer a small, already-pulled model for a snappy honest demo.
    model = os.environ.get("AIDA_DEMO_MODEL") or "llama3.2"
    config = dict(config)
    config["model_name"] = model
    config["base_model"] = config.get("base_model") or model
    config["voice_enabled"] = False
    config["live_deliberation_enabled"] = False
    config["deliberation_enabled"] = False

    storage._DB_PATH = Path(os.environ["SEEDLING_DB_PATH"])
    storage._db = None
    storage.init_db()

    # Clear the shell's launcher line so the GIF opens on the session itself.
    print("\033[2J\033[H", end="", flush=True)

    _say("Aida — live session (demo capture)")
    _say(ui.dim(f"model={model}  ·  isolated temp DB  ·  voice off"))
    _say("")

    # --- Session 1: teach a fact, get a reply, end ---
    _say(ui.dim("── session 1 (fresh) ──"))
    s1 = _build_session(config, fresh=True, model=model)
    s1.start()
    _say("[Fresh session — no prior context]")
    _say("")

    _type_user("Remember that I live in Mebane, North Carolina.")
    _stream_reply(s1, "Remember that I live in Mebane, North Carolina.")
    _pause(0.6)
    _say("")

    _type_user("Where do I live? One short sentence.")
    _stream_reply(s1, "Where do I live? One short sentence.")
    _pause(0.6)
    _say("")

    # Persona was persisted live on "Remember…" — no heavy end()-pass needed
    # for the restore proof (keeps the GIF short and honest).
    _say(ui.dim("[persona saved live · session closed]"))
    _say("")
    _pause(0.8)

    # --- Session 2: restore memory ---
    _say(ui.dim("── session 2 (restore) ──"))
    s2 = _build_session(config, fresh=False, model=model)
    s2.start()
    _say("[Context restored]")
    _say("")

    _type_user("Where do I live?")
    _stream_reply(s2, "Where do I live?")
    _pause(0.6)
    _say("")

    _say(ui.dim("[session closed]"))
    _say("")
    _say(ui.dim("Recorded from a live local session — not an illustration."))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    finally:
        try:
            shutil.rmtree(_TMP, ignore_errors=True)
        except Exception:
            pass
