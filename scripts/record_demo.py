#!/usr/bin/env python3
"""Record a REAL Aida session for docs/assets/demo.gif (isolated temp DB).

Drives the same ThreadSession path as chat, against a throwaway database, so
the GIF is a live capture — not an AI-illustrated recreation. Used by
docs/assets/demo.tape via VHS:

    vhs docs/assets/demo.tape

Or run standalone to preview:

    AIDA_VOICE=0 ./.venv/bin/python scripts/record_demo.py

WHAT THIS CAPTURES (and why these four beats)
---------------------------------------------
The GIF used to demo *memory* ("teach a fact -> restore it"). Memory is now
one feature among several; the claim the project actually leads with is
mutualism: **you own truth, and she won't pretend.** So the capture walks the
four beats that carry that claim — and every one of them is DETERMINISTIC or
guard-enforced, so the GIF reproduces:

  1. TEACH      "Remember..." promotes to the persona layer, saved live.
  2. CORRECT    Your correction prunes by *your* words. She keeps your phrasing;
                the model is never asked which fact to delete.
  3. WON'T      Asked about a link she cannot open, she declines to invent it
     PRETEND    and asks you to paste it. This is the behaviour the ablation
                measures (~20% -> 0% confabulation, guards on).
  4. RESTORE    A fresh session picks all of it up — corrected, not original.

DELIBERATELY NOT IN THE GIF: the collaborative wall ("I'm not sure. Help me
decide?"). It is gated by wall.py/wallgate.py on real lagged CRITIC signals and
is conservative BY DESIGN — it fires on the rare genuinely-hard turn. Forcing it
for a recording would mean lowering `wall_act_cutoff` until it fired, i.e.
staging the one moment whose whole value is that it is not staged. If a wall
capture is ever wanted, record an unscripted session and keep the take where it
fires — do not tune the cutoff to manufacture one.
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

# A URL that is real, stable, and unreachable from an offline runtime. She must
# decline to characterise its contents rather than guess from the slug.
#
# PHRASING MATTERS HERE, AND THAT IS A FINDING — NOT A CONVENIENCE.
# Two takes on llama3.2, same guards, same URL:
#   "What does <URL> say about X?"        -> correct refusal + offer to paste
#   "Summarize what <URL> says."          -> INVENTED the page's contents
# For natural-language asks there is no deterministic refusal: filereader's
# _NL_BLOCKED only stops the RUNTIME from attaching a URL, then the turn falls
# through to the model, where the refusal is GUARD_TEXT-mediated (probabilistic).
# Only the explicit `:read` path is code-enforced.
#
# So this beat is honest but not guaranteed. If a take confabulates here, the
# right response is to harden the guard and re-record — never to keep hunting
# phrasings until one behaves.
UNREACHABLE_URL = "https://github.com/ml-explore/mlx-lm/blob/main/README.md"


def _pause(s: float = 0.55) -> None:
    time.sleep(s)


def _say(line: str = "") -> None:
    print(line, flush=True)
    _pause(0.25)


def _beat(n: int, title: str) -> None:
    """Label each act so a viewer reads the claim, not just the transcript."""
    _say("")
    _say(ui.dim(f"── {n}. {title} ──"))
    _say("")


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
        # Left OFF on purpose — see the module docstring. The wall is not
        # scriptable without rigging its cutoff.
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


def _turn(session: ThreadSession, prompt: str, *, gap: float = 0.6) -> str:
    _type_user(prompt)
    text = _stream_reply(session, prompt)
    _pause(gap)
    return text


def main() -> int:
    cfg_path = ROOT / "config.yaml"
    config = yaml.safe_load(cfg_path.read_text()) if cfg_path.exists() else {}
    # Prefer a small, already-pulled model for a snappy honest demo. The point
    # of using the 3B is that the guards — not model scale — do the work.
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
    _say(ui.dim(f"model={model}  ·  isolated temp DB  ·  offline  ·  voice off"))

    # --- Session 1: teach, correct, refuse to invent ---
    s1 = _build_session(config, fresh=True, model=model)
    s1.start()
    _say("")
    _say(ui.dim("[fresh session — no prior context]"))

    _beat(1, "You teach her. It saves live.")
    _turn(s1, "Remember that I live in Mebane, North Carolina.")

    # The correction must change the VALUE, not the formatting. An earlier take
    # corrected "North Carolina" to "NC", which stored a stilted fact and made
    # beat 4 land on a technicality instead of a visibly different answer.
    #
    # The second turn answers a DISAMBIGUATION MENU. When a correction does not
    # unambiguously match one stored fact, the runtime refuses to pick for you
    # and prints a numbered list instead. An earlier take left that menu
    # unanswered, so the correction silently never applied. Answering it is not
    # a workaround — it IS the guarantee ("the model never guess-deletes")
    # happening in the memory layer, and unlike the collaborative wall it is
    # deterministic, so it belongs in the capture.
    _beat(2, "You correct her. She won't guess WHICH fact to drop.")
    _turn(s1, "That's wrong — I moved. The correct city is Durham, North Carolina.")
    _turn(s1, "0")
    _turn(s1, "Where do I live? One short sentence.")

    _beat(3, "She won't pretend. Offline is a boundary.")
    _turn(s1, f"What does {UNREACHABLE_URL} say about installation?", gap=0.8)

    _say("")
    _say(ui.dim("[persona saved live · session closed]"))
    _pause(0.8)

    # --- Session 2: restore the CORRECTED fact, not the original ---
    _beat(4, "New session. The CORRECTED fact is there — not the original.")
    s2 = _build_session(config, fresh=False, model=model)
    s2.start()
    _say(ui.dim("[context restored]"))
    _say("")
    _turn(s2, "Where do I live?")

    _say("")
    _say(ui.dim("[session closed]"))
    _say("")
    _say(ui.dim("Recorded from a live local session — not an illustration."))
    _say(ui.dim("Reproduce: bash run.sh smoke  ·  bash run.sh confab-eval"))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    finally:
        try:
            shutil.rmtree(_TMP, ignore_errors=True)
        except Exception:
            pass
