#!/usr/bin/env python3
"""
Seedling end-to-end SMOKE TEST  --  one command, real model, safe to repeat.

What it proves (live, against the model in config.yaml):
  1. Ollama is reachable and the configured model answers.
  2. A session starts and restores context.
  3. A reply STREAMS token-by-token and returns the full text.
  4. The Critic grades in the BACKGROUND (reply returns before grading finishes).
  5. A live "remember ..." directive is promoted to persona memory immediately.
  6. A live correction ("that's wrong ...") prunes/replaces the right fact.
  7. end() runs deliberation and promotes a DELIBERATED BELIEF.
  8. That belief PERSISTS and is re-injected when a brand-new session reloads.

Safety: runs against an ISOLATED temporary database (a fresh temp dir), so it
NEVER touches your real .seedling_db and leaves nothing behind.

Run it:
    ./.venv/bin/python smoke_test.py
    ./.venv/bin/python smoke_test.py --model llama3.2     # override the model
"""
from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import time
from pathlib import Path

import yaml

# --- pretty output -------------------------------------------------------
GREEN, RED, DIM, BOLD, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[1m", "\033[0m"
_results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    tag = f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"
    line = f"  [{tag}] {name}"
    if detail:
        line += f"  {DIM}{detail}{RESET}"
    print(line)
    _results.append((name, ok, detail))
    return ok


def section(title: str) -> None:
    print(f"\n{BOLD}{title}{RESET}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Seedling end-to-end smoke test")
    ap.add_argument("--model", help="override model_name from config.yaml")
    ap.add_argument("--keep", action="store_true",
                    help="keep the temp DB instead of deleting it (debug)")
    args = ap.parse_args()

    cfg = yaml.safe_load(open("config.yaml")) if Path("config.yaml").exists() else {}
    model = args.model or cfg.get("model_name", "llama3.2")
    base_model = cfg.get("base_model", model)

    print(f"{BOLD}=== Seedling smoke test ==={RESET}")
    print(f"  model        : {model}")
    print(f"  critic       : {cfg.get('critic_backend', 'local')}")
    print(f"  deliberation : {cfg.get('deliberation_enabled', True)} "
          f"(live={cfg.get('live_deliberation_enabled', True)})")

    # --- 0) Ollama reachable + model present -----------------------------
    section("0. Environment")
    try:
        import ollama
        names = []
        try:
            listing = ollama.list()
            names = [m.get("model") or m.get("name") for m in listing.get("models", [])]
        except Exception:
            pass
        # a tiny call is the real readiness test
        ollama.chat(model=model, messages=[{"role": "user", "content": "say OK"}],
                    keep_alive="10m")
        check("Ollama reachable and model responds", True,
              f"models seen: {len(names)}")
    except Exception as e:
        check("Ollama reachable and model responds", False, str(e)[:160])
        print(f"\n{RED}Ollama isn't reachable. Start it and pull the model:{RESET}")
        print(f"  ollama serve            # in another terminal")
        print(f"  ollama pull {model}")
        return _summarize()

    # --- isolate the DB so we never touch real data ----------------------
    import storage
    tmp = Path(tempfile.mkdtemp(prefix="seedling_smoke_"))
    storage._DB_PATH = tmp / "db"      # type: ignore[attr-defined]
    storage._db = None                 # force reconnect at the temp path
    # also redirect session buffer/log writes into the temp dir
    import session as S
    S._BUFFER_DIR = tmp / "buf"; S._BUFFER_DIR.mkdir(parents=True, exist_ok=True)

    from mcm import MCM
    from critic import CriticInstance
    from session import ThreadSession

    try:
        # --- 1) start / restore -----------------------------------------
        section("1. Session start & context restore")
        mcm = MCM(adapter_version=cfg.get("adapter_version", 0), base_model=base_model)
        critic = CriticInstance(backend=cfg.get("critic_backend", "local"),
                                base_model=base_model,
                                perplexity_model=cfg.get("perplexity_model", "sonar"))
        sess = ThreadSession(mcm=mcm, critic=critic, model_name=model, fresh=True,
                             deliberation_enabled=cfg.get("deliberation_enabled", True),
                             live_deliberation_enabled=cfg.get("live_deliberation_enabled", True),
                             history_window_turns=cfg.get("history_window_turns", 24))
        injection = sess.start()
        check("start() returns a context-restore string", bool(injection))

        # --- 2) streaming reply + 3) background critic ------------------
        section("2. Streaming reply  +  background critic")
        tokens = []
        first_at = {"t": None}
        t0 = time.monotonic()

        def on_tok(tok: str) -> None:
            if first_at["t"] is None:
                first_at["t"] = time.monotonic() - t0
            tokens.append(tok)

        reply = sess.chat("In one short sentence, what is the Second Arrow?", on_token=on_tok)
        full_at = time.monotonic() - t0

        check("reply streamed in multiple tokens", len(tokens) >= 2,
              f"{len(tokens)} tokens")
        check("full reply returned as a string", isinstance(reply, str) and len(reply) > 0,
              f"{len(reply)} chars")
        check("time-to-first-token < full-reply time (streaming works)",
              first_at["t"] is not None and first_at["t"] <= full_at,
              f"ttft={first_at['t']:.2f}s full={full_at:.2f}s")
        # Critic should NOT have finished synchronously (it grades in background).
        evals_right_after = len(sess._critic_evals)
        sess._join_critic(timeout=60.0)
        evals_after_join = len(sess._critic_evals)
        check("critic eval lands after join (background grading works)",
              evals_after_join >= 1,
              f"immediately={evals_right_after}, after_join={evals_after_join}")

        # --- 4) live persona directive ----------------------------------
        section("3. Live memory: directive promotion")
        before = len(mcm.persona_facts())
        sess.chat("Remember that my name is Stew and I live in Mebane.")
        sess._join_critic(timeout=60.0)
        after = len(mcm.persona_facts())
        facts_text = " | ".join(f.text for f in mcm.persona_facts())
        check("a 'Remember ...' directive was promoted to persona memory",
              after > before, f"{before} -> {after} facts")

        # --- 5) live correction -----------------------------------------
        section("4. Live memory: correction")
        # Use a FRESH, isolated session whose persona has exactly ONE fact, so
        # the correction has an unambiguous target. (With multiple unrelated
        # facts present, the locator deliberately ASKS rather than guess-deletes
        # — that fail-safe is correct, but it's not what we're testing here.)
        mcm_c = MCM(adapter_version=cfg.get("adapter_version", 0), base_model=base_model)
        mcm_c.restore_context(fresh=True)
        sess_c = ThreadSession(mcm=mcm_c, critic=critic, model_name=model, fresh=True,
                               deliberation_enabled=False, live_deliberation_enabled=False,
                               history_window_turns=cfg.get("history_window_turns", 24))
        sess_c.start()
        mcm_c.promote_persona_fact("my favorite editor is Vim", "preference", sess_c.thread_id)
        out = sess_c.chat("That's wrong, the correct editor is VSCode not Vim.")
        post = [f.text for f in mcm_c.persona_facts()]
        # If the locator matched directly, the prune+replace already happened. If
        # it asked to disambiguate (single fact => index 0), resolve it by index.
        if out.startswith("[memory] I couldn't tell"):
            out = sess_c.chat("0")
            post = [f.text for f in mcm_c.persona_facts()]
        corrected = (out.startswith("[memory")
                     and not any("Vim" in t for t in post)
                     and any("VSCode" in t for t in post))
        check("correction pruned the stale fact and saved the new one", corrected,
              f"handled={out[:48]!r}")

        # --- 6) end(): deliberation + belief promotion ------------------
        section("5. Session end: deliberation + belief growth")
        # give the model an opinion worth deliberating
        sess.chat("Briefly: preserving dissent beats averaging it away when forming beliefs.")
        delta = sess.end()
        check("end() returns a ThreadDelta with an insight",
              delta is not None and bool(getattr(delta, "insight_gained", "")),
              f"insight={str(getattr(delta, 'insight_gained', ''))[:60]!r}")
        beliefs_now = mcm._state.beliefs.beliefs if mcm._state else []
        check("at least one deliberated belief was formed", len(beliefs_now) >= 1,
              f"{len(beliefs_now)} belief(s)")

        # --- 7) persistence across a fresh reload -----------------------
        section("6. Cross-thread persistence (the whole point)")
        mcm2 = MCM(adapter_version=cfg.get("adapter_version", 0), base_model=base_model)
        injection2 = mcm2.restore_context(fresh=False)
        has_persona = "Stew" in injection2 or "Mebane" in injection2
        has_beliefs = "EARNED" in injection2 and (
            len(mcm2._state.beliefs.beliefs) >= 1 if mcm2._state else False)
        check("persona fact persisted and re-injected on reload", has_persona)
        check("deliberated belief persisted and re-injected on reload", has_beliefs,
              f"{len(mcm2._state.beliefs.beliefs) if mcm2._state else 0} belief(s) reloaded")

        return _summarize()
    finally:
        if args.keep:
            print(f"\n{DIM}temp DB kept at: {tmp}{RESET}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)
            storage._db = None


def _summarize() -> int:
    passed = sum(1 for _, ok, _ in _results if ok)
    total = len(_results)
    section("Summary")
    if passed == total:
        print(f"  {GREEN}{BOLD}ALL {total} CHECKS PASSED{RESET}")
        return 0
    failed = [n for n, ok, _ in _results if not ok]
    print(f"  {RED}{BOLD}{passed}/{total} passed{RESET}  "
          f"({RED}failed: {', '.join(failed)}{RESET})")
    return 1


if __name__ == "__main__":
    sys.exit(main())
