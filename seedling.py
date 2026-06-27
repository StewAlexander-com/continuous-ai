"""
seedling.py — Seedling Runtime Entry Point (CLI)

Commands:
  python seedling.py chat              start session with context restore
  python seedling.py chat --fresh      start session with no prior context
  python seedling.py snapshot          manual snapshot of current MCM state
  python seedling.py status            print current MCM state summary
  python seedling.py eval              run evaluation report
  python seedling.py tune              show tuning score table
  python seedling.py tune --approve-tuning  run LoRA adapter update (DESTRUCTIVE)

Environment:
  PERPLEXITY_API_KEY   — required for critic_backend=perplexity (recommended)

Requires:
  pip install ollama lancedb pyarrow pyyaml httpx
  ollama pull llama3.2
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import yaml

import ui
import inputsafe

_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE))


def _load_config() -> dict:
    config_path = _HERE / "config.yaml"
    if config_path.exists():
        with open(config_path) as f:
            return yaml.safe_load(f) or {}
    return {}


class _ThinkingIndicator:
    """A tiny blinking 'Aida is working...' line shown ONLY while waiting for the
    first token of a reply. It runs on a daemon thread, animates the dots, and
    erases itself the moment it's stopped (when streaming starts or the turn
    ends) so it never overlaps the answer. Purely cosmetic: it reports that the
    session is alive and doing work — not a claim about cognition.

    Honest by construction: it can only appear when there's a real wait, and it
    vanishes the instant real output arrives.
    """
    def __init__(self, enabled: bool = True, label: str = "Aida is working"):
        import threading
        self._enabled = enabled
        self._label = label
        self._stop = threading.Event()
        self._thread: "threading.Thread | None" = None
        self._lock = threading.Lock()
        self._cleared = False

    def start(self) -> None:
        if not self._enabled:
            return
        import threading
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        import time
        frames = [".  ", ".. ", "...", " ..", "  .", "   "]
        i = 0
        # small delay so quick replies never flash the indicator at all
        if self._stop.wait(0.35):
            return
        while not self._stop.is_set():
            with self._lock:
                if self._cleared:
                    return
                sys.stdout.write("\r" + ui.dim(f"{self._label}{frames[i % len(frames)]}"))
                sys.stdout.flush()
            i += 1
            if self._stop.wait(0.25):
                break

    def stop(self) -> None:
        if not self._enabled:
            return
        self._stop.set()
        with self._lock:
            if not self._cleared:
                # erase the whole line so the reply prints cleanly
                sys.stdout.write(ui.clear_full_line())
                sys.stdout.flush()
                self._cleared = True


def _setup_logging(level: str = "INFO") -> None:
    """Quiet the terminal, keep the full trail on disk.

    The chat view should read like a conversation, not a server log. So:
      * the FILE (logs/seedling.log) gets EVERYTHING at the configured level
        (default INFO) -- httpx requests, storage/mcm/session internals,
        deliberation rounds -- for anyone who wants to dive deeper.
      * the CONSOLE only shows WARNING and above (real problems), so routine
        INFO chatter never bleeds into the conversation.
    Set `log_level: DEBUG` in config.yaml (or LOG_CONSOLE=1) to also see INFO on
    screen when you're debugging.
    """
    import os
    log_dir = _HERE / "logs"
    log_dir.mkdir(exist_ok=True)
    file_level = getattr(logging, level.upper(), logging.INFO)

    root = logging.getLogger()
    root.setLevel(min(file_level, logging.INFO))
    # Clear any handlers from a prior call so this is idempotent.
    for h in list(root.handlers):
        root.removeHandler(h)

    fmt = logging.Formatter("%(asctime)s  %(levelname)-8s  %(name)s: %(message)s")

    file_handler = logging.FileHandler(log_dir / "seedling.log")
    file_handler.setLevel(file_level)
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    console = logging.StreamHandler(sys.stderr)
    # Console stays quiet by default; opt into verbosity for debugging.
    console_verbose = os.environ.get("LOG_CONSOLE") == "1" or level.upper() == "DEBUG"
    console.setLevel(logging.INFO if console_verbose else logging.WARNING)
    console.setFormatter(fmt)
    root.addHandler(console)

    # httpx logs one INFO line per request -- always keep that off the console
    # (it lands mid-conversation). It still reaches the file via the root logger.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def _chat_options_from_config(config: dict) -> dict:
    """Build the Ollama generation-options dict for the chat model from config.
    Only includes keys that are actually set, so an unset value never overrides
    a model default (zero behavior change unless the user opts in). Tunables:
      num_predict (cap output length), num_ctx (context window), temperature,
      top_p, num_thread. Lives under `chat_options:` in config.yaml."""
    raw = config.get("chat_options") or {}
    allowed = ("num_predict", "num_ctx", "temperature", "top_p", "top_k", "num_thread")
    return {k: raw[k] for k in allowed if k in raw and raw[k] is not None}


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def _handle_model_command(session, user_input: str) -> None:
    """Handle the in-chat ':model' command (ephemeral switch for this session).

    Bare ':model'            -> list installed models (numbered, current marked).
    ':model <name>'          -> switch to that exact tag (auto-pulls if missing).
    ':model <number>'        -> switch to the Nth model from the listing.

    Thin dispatcher only: the real swap lives in ThreadSession.switch_model so it
    stays testable. Never edits config.yaml -- config remains the default.
    """
    from session import _installed_model_names
    arg = user_input[len(":model"):].strip()
    installed = _installed_model_names()

    if not arg:
        # Bare ':model' -> show what's available, mark the current one.
        if not installed:
            print("  " + ui.dim("[Could not list models. Switch by exact tag: :model qwen2.5:7b]") + "\n")
            return
        print("  " + ui.dim("Installed models (':model <number>' or ':model <name>' to switch):"))
        for i, name in enumerate(installed, 1):
            mark = "  <- current" if name == session.model_name else ""
            print("    " + ui.dim(f"{i}. {name}{mark}"))
        print()
        return

    # Numeric choice resolves against the listing.
    target = arg
    if arg.isdigit() and installed:
        idx = int(arg)
        if 1 <= idx <= len(installed):
            target = installed[idx - 1]
        else:
            print("  " + ui.dim(f"[No model #{idx}. There are {len(installed)} installed. Type ':model' to list.]") + "\n")
            return

    # If the target isn't installed, warn BEFORE the (blocking) pull and then
    # stream progress so a multi-GB download never looks like a hang.
    needs_pull = bool(installed) and target not in installed
    if needs_pull:
        print(f"  \033[2mModel '{target}' not installed \u2014 pulling now "
              "(one-time download; 7-14B models are ~4-9GB)\u2026\033[0m")

    # The live percent line uses carriage-return overwrite, which only makes
    # sense on a real terminal. On a non-TTY (piped/redirected output) it would
    # emit corrupt \r + ANSI junk, so we go quiet there — the heads-up above and
    # the result below still print on any stream, so nothing is lost but the
    # live animation. Same TTY contract as _ThinkingIndicator(enabled=isatty()).
    _tty = sys.stdout.isatty()
    _last = {"pct": -1, "status": ""}

    def _label(status: str) -> str:
        # Ollama reports the download phase as 'pulling <layer-digest>' (a long
        # hex hash that's just noise to a human). Collapse those to 'downloading'.
        if status.startswith("pulling ") and status != "pulling manifest":
            return "downloading"
        return status

    def _progress(status: str, completed: int, total: int) -> None:
        if not _tty:
            return  # no carriage-return animation on non-interactive output
        # Re-draw a single line in place. Only repaint on a status change or a
        # whole-percent change, so we don't spam the terminal.
        if total and total > 0:
            pct = int(completed * 100 / total)
            if pct == _last["pct"] and status == _last["status"]:
                return
            _last["pct"], _last["status"] = pct, status
            gb = total / 1e9
            sys.stdout.write("\r  " + ui.dim(f"{_label(status)}: {pct:3d}%  ({gb:.1f} GB)") + "   ")
        else:
            if status == _last["status"]:
                return
            _last["status"] = status
            sys.stdout.write(f"\r  \033[2m{_label(status)}\u2026\033[0m   ")
        sys.stdout.flush()

    ok, msg = session.switch_model(target, progress=_progress if needs_pull else None)
    if needs_pull and _tty:
        sys.stdout.write(ui.clear_line())  # clear the progress line before the result (TTY only)
        sys.stdout.flush()
    color = "2" if ok else "33"   # dim if ok, yellow if it failed/kept current
    print("  " + ui.colored(msg, color) + "\n")


def _stream_turn(session, turn_text: str) -> None:
    """Send turn_text to the model and stream the reply (shared by :read/:more)."""
    _state = {"started": False}
    _spinner = _ThinkingIndicator(enabled=sys.stdout.isatty())
    _spinner.start()

    def _on_token(tok: str) -> None:
        if not _state["started"]:
            _spinner.stop()
            sys.stdout.write(ui.reply_prefix_inline())
            _state["started"] = True
        sys.stdout.write(tok)
        sys.stdout.flush()

    try:
        response = session.chat(turn_text, on_token=_on_token)
    finally:
        _spinner.stop()
    if _state["started"]:
        print("\n")
    else:
        print(f"{ui.reply_prefix_inline()}{response}\n")


def _config_num_ctx(config: dict):
    """Pull num_ctx from chat_options if set, else None (Ollama default)."""
    opts = config.get("chat_options") or {}
    return opts.get("num_ctx")


def _parse_read_arg(arg: str) -> tuple[str, str | None]:
    """Split ':read' argument into (path, optional_question).

    The first whitespace-delimited token is the path; anything after it is treated
    as an optional question/comment about the file. Quoting is honored so paths
    WITH spaces work: :read "~/My Notes.txt" summarize it. This fixes the trap
    where ':read foo.py what is this' fed the whole phrase to the filesystem.
    """
    import shlex
    arg = (arg or "").strip()
    if not arg:
        return "", None
    try:
        tokens = shlex.split(arg)          # quote-aware
    except ValueError:
        tokens = arg.split()               # unbalanced quotes -> simple split
    if not tokens:
        return "", None
    path = tokens[0]
    question = " ".join(tokens[1:]).strip() or None
    return path, question


def _handle_read_command(session, user_input: str, config: dict, read_state: dict) -> None:
    """Handle ':read <path>' — attach a local text/py/csv file as the turn.

    The runtime (filereader) reads the named file deterministically; its REAL
    contents are fed in as a normal graded turn. Large text/py files are shown in
    a context-budgeted CHUNK; ':more' pages forward. CSV is a structural summary
    (not paged). The model never reaches files on its own; every partial view
    carries an explicit paging notice so it can't characterize unseen content.
    """
    import filereader
    arg = user_input[len(":read"):].strip()
    path, question = _parse_read_arg(arg)
    ok, name_or_err, text = filereader.load_file(path, max_mb=config.get("max_attach_mb"))
    if not ok:
        print("  " + ui.warn(name_or_err) + "\n")   # yellow: honest read error, no turn
        read_state.clear()
        return
    name = name_or_err

    # If the user appended a question/comment, ask it; else a generic orient prompt.
    ask = (f"\n\nThe user attached this file and asks: {question}"
           if question else
           "\n\nThe user attached this file. Briefly say what it is and what you "
           "can help with; then await their question.")

    if filereader.is_csv(name):
        block = filereader.format_csv_block(text, name)
        read_state.clear()   # CSV summary is complete; nothing to page
        print("  " + ui.dim(f"[attached {name} — CSV summary]"))
        _stream_turn(session, block + ask)
        return

    budget = filereader.budget_chars(_config_num_ctx(config))
    chunk = filereader.read_chunk(text, name, char_offset=0, budget=budget)
    # Cache the full decoded text + where we are, so :more pages from memory.
    read_state.clear()
    read_state.update({"name": name, "text": text, "offset": chunk["next_offset"],
                       "total": chunk["total"], "budget": budget, "done": chunk["done"]})
    tail = "" if chunk["done"] else " (type ':more' for the next part)"
    print("  " + ui.dim(f"[attached {name} — chunk {chunk['chunk_no']}{tail}]"))
    _stream_turn(session, chunk["block"] + ask)


def _handle_more_command(session, read_state: dict) -> None:
    """Handle ':more' — reveal the next chunk of the currently-attached file."""
    import filereader
    if not read_state or not read_state.get("text"):
        print("  " + ui.dim("[nothing to continue — attach a file with ':read <path>' first]") + "\n")
        return
    if read_state.get("done"):
        print("  " + ui.dim(f"[that was the whole of {read_state.get('name','the file')} — nothing more to show]") + "\n")
        return
    chunk = filereader.read_chunk(read_state["text"], read_state["name"],
                                  char_offset=read_state["offset"], budget=read_state["budget"])
    read_state["offset"] = chunk["next_offset"]
    read_state["done"] = chunk["done"]
    tail = "" if chunk["done"] else " (':more' for more)"
    print("  " + ui.dim(f"[{read_state['name']} — chunk {chunk['chunk_no']}{tail}]"))
    _stream_turn(session, chunk["block"] + "\n\nThis is the next part of the file the "
                 "user attached. Continue from here; await their question.")


def cmd_chat(config: dict, fresh: bool = False) -> None:
    """Start an interactive chat session."""
    from mcm import MCM
    from critic import CriticInstance
    from session import ThreadSession

    mcm = MCM(
        adapter_version=config.get("adapter_version", 0),
        base_model=config.get("base_model", "llama3.2"),
    )
    critic = CriticInstance(
        backend=config.get("critic_backend", "local"),
        base_model=config.get("base_model", "llama3.2"),
        perplexity_model=config.get("perplexity_model", "sonar"),
    )
    session = ThreadSession(
        mcm=mcm,
        critic=critic,
        model_name=config.get("model_name", "llama3.2"),
        fresh=fresh,
        tuning_threshold_n=config.get("tuning_threshold_n", 10),
        deliberation_enabled=config.get("deliberation_enabled", True),
        live_deliberation_enabled=config.get("live_deliberation_enabled", True),
        history_window_turns=config.get("history_window_turns", 24),
        live_annotation_enabled=config.get("live_annotation_enabled", False),
        chat_options=_chat_options_from_config(config),
        deliberation_drain_timeout_s=config.get("deliberation_drain_timeout_s", 90.0),
    )

    print("\n" + "="*60)
    print("  SEEDLING — Local AI Continuity Runtime")
    print("="*60)
    if not os.environ.get("PERPLEXITY_API_KEY") and config.get("critic_backend") == "perplexity":
        print("  ⚠  PERPLEXITY_API_KEY not set — critic will fall back to local")
        print("  Set it with: export PERPLEXITY_API_KEY=pplx-...")
    print()

    context_injection = session.start()
    # Warm the model now (one tiny generation) so the FIRST real turn doesn't pay
    # the cold-load cost. Best-effort; failures are ignored.
    session.warmup()
    if fresh:
        print("[Fresh session — no prior context]\n")
    else:
        print("[Context restored]\n")

    print("Type 'exit' or 'quit' to end the session.")
    print("Type ':model' to list/switch models mid-session (chat + critic; context kept).")
    print("Type ':read <path>' to attach a text/python/CSV file; ':more' pages through large files.")
    print("(Type a line and press Enter to send. Pasting multiple lines sends them "
          "as one turn. Commands like :model and exit are single-line.)\n")
    read_state: dict = {}   # paging state for the currently-attached file (:read/:more)

    try:
        while True:
            try:
                raw = inputsafe.read_multiline("You: ")
            except EOFError:
                break
            if raw is None:       # Ctrl-C cancelled the block -> re-prompt
                continue

            # Harden EVERY turn: strip terminal escapes, control bytes, hidden/
            # bidi Unicode, and cap size (loud truncation). Preserves code, CSV,
            # newlines, tabs. See inputsafe.py for the threat model.
            user_input, _notices = inputsafe.sanitize_input(raw)
            for _n in _notices:
                print("  " + ui.dim(f"[input: {_n}]"))

            # Commands/quit are recognized ONLY on a single-line input; a
            # multi-line block is always one chat turn (a pasted line can't
            # switch models or quit). read_multiline already enforces this, but
            # we strip a lone trailing newline for clean command matching.
            is_single_line = "\n" not in user_input.strip()
            user_input = user_input.strip() if is_single_line else user_input.strip("\n")

            # Commands & quit are recognized ONLY on single-line input. A pasted
            # multi-line block is ALWAYS a chat turn — it can never quit the
            # session or switch models (defense in depth alongside read_multiline).
            if is_single_line:
                if user_input.lower() in ("exit", "quit", "q", ":q"):
                    break
                # In-chat model switch (ephemeral, this session only). Bare
                # ':model' lists installed models; ':model <name|number>'
                # switches chat + critic together. config.yaml stays the default.
                if user_input.lower() == ":model" or user_input.lower().startswith(":model "):
                    _handle_model_command(session, user_input)
                    continue
                # In-chat file attach. The RUNTIME reads a user-named local file
                # and feeds its real contents in as the turn — the model never
                # reaches files on its own (the guard still forbids that).
                if user_input.lower() == ":read" or user_input.lower().startswith(":read "):
                    _handle_read_command(session, user_input, config, read_state)
                    continue
                # ':more' pages forward through the currently-attached file.
                if user_input.lower() == ":more":
                    _handle_more_command(session, read_state)
                    continue
            if not user_input:
                continue

            # Stream the reply token-by-token so it appears immediately. The
            # 'Model: ' prefix is printed once on the first real token; memory
            # corrections short-circuit before any token, so _streamed stays
            # False and we render them as a dim system line instead.
            _stream_state = {"started": False}

            # --- live "working" indicator while we wait for the FIRST token ---
            # The reply streams, so the only real wait is before the first token
            # arrives. A blinking indicator reassures the user the session is
            # alive and working; it ERASES ITSELF the instant streaming begins,
            # so it never overlaps the answer. Pure CLI display — no effect on
            # logic or the reply path. Skipped on non-TTY (piped) output.
            _spinner = _ThinkingIndicator(enabled=sys.stdout.isatty())
            _spinner.start()

            def _on_token(tok: str) -> None:
                if not _stream_state["started"]:
                    _spinner.stop()                 # clear the indicator first
                    sys.stdout.write(ui.reply_prefix_inline())
                    _stream_state["started"] = True
                sys.stdout.write(tok)
                sys.stdout.flush()

            try:
                response = session.chat(user_input, on_token=_on_token)
            finally:
                _spinner.stop()                     # also clears the [memory]/no-stream paths

            if response.startswith("[memory"):
                # No tokens were streamed; render the confirmation line.
                print("\n" + ui.dim(response) + "\n")
            else:
                if _stream_state["started"]:
                    print("\n")          # close the streamed line
                else:
                    print(f"{ui.reply_prefix_inline()}{response}\n")   # fallback if nothing streamed
                # Surface any live persona writes that happened this turn.
                for notice in getattr(session, "_memory_notices", []):
                    print("  " + ui.dim(notice))
                # Honest mechanism trace: show what background work this turn
                # kicked off. We only say work STARTED (the deliberation runs
                # async; its outcome is summarized at session end) — this shows
                # the machinery, never claims the model is "thinking".
                act = getattr(session, "_turn_activity", {})
                bits = []
                if act.get("graded"):
                    bits.append("grading reply")
                if act.get("deliberating"):
                    bits.append("deliberating in background")
                if bits:
                    # Activity line is on by DEFAULT: it's short, honest, and
                    # says only that background work STARTED. Routed through ui
                    # so it respects NO_COLOR / non-TTY.
                    sep = " \u00b7 "
                    print("  " + ui.dim("\u231f " + sep.join(bits)))
                # Operational voice: a dim, one-line readout of Aida's measured
                # working state. This is OPT-OUT-by-omission (verbose only): the
                # TONE already shows through her reply, so the extra line stays
                # quiet by default to avoid clutter for power users. Enable with
                # LOG_CONSOLE=1, log_level: DEBUG, or AIDA_SHOW_STATUS=1.
                _verbose = (os.environ.get("LOG_CONSOLE") == "1" or
                            os.environ.get("AIDA_SHOW_STATUS") == "1" or
                            str(config.get("log_level", "")).upper() == "DEBUG")
                if _verbose:
                    try:
                        import voice
                        from datetime import datetime, timezone
                        wu = len(getattr(session, "_critic_evals", [])) + \
                            getattr(session, "_deliberation_count", 0)
                        nt = sum(1 for m in getattr(session, "_messages", [])
                                 if m.get("role") == "assistant")
                        st = voice.compute_state(
                            now=datetime.now(timezone.utc),
                            session_start=getattr(session, "_session_start",
                                                  datetime.now(timezone.utc)),
                            substantive_turns=nt, work_units=wu)
                        print("  " + ui.dim(voice.status_line(st)))
                    except Exception:
                        pass

    except KeyboardInterrupt:
        print("\n[Interrupted]")
    finally:
        delta = session.end()
        print(f"\n[Session ended]")
        print(f"  Insight logged : {delta.insight_gained[:80]}")
        print(f"  Coherence      : {delta.coherence_score:.2f}")
        print(f"  Emergent       : {delta.emergent}")
        if delta.emergent and delta.emergent_detail:
            print(f"  Emergent detail: {delta.emergent_detail[:80]}")
        # Honest 'internal work this session' summary (mechanism, not mind).
        s = getattr(session, "_end_summary", {}) or {}
        if s:
            print(
                f"  Internal work  : {s.get('deliberations', 0)} deliberation(s)"
                f" · {s.get('contested', 0)} contested"
                f" · {s.get('pruned', 0)} pruned"
            )
            print(
                f"  Beliefs        : {s.get('active_beliefs', 0)} active"
                f" · {s.get('archived_beliefs', 0)} archived (quarantined, revivable)"
            )

        if config.get("snapshot_on_exit", True):
            mcm.graceful_pause(notes="Normal session end")


def cmd_snapshot(config: dict) -> None:
    """Manual snapshot of current MCM state."""
    from mcm import MCM
    mcm = MCM(
        adapter_version=config.get("adapter_version", 0),
        base_model=config.get("base_model", "llama3.2"),
    )
    mcm.restore_context()
    mcm.graceful_pause(notes="Manual snapshot via CLI")
    print("[Snapshot written to snapshots/]")


def cmd_status(config: dict) -> None:
    """Print current MCM state summary."""
    from mcm import MCM
    mcm = MCM(
        adapter_version=config.get("adapter_version", 0),
        base_model=config.get("base_model", "llama3.2"),
    )
    mcm.restore_context()
    print(mcm.summary())
    print(f"\n  config.yaml:")
    for k, v in config.items():
        if k != "eval_thresholds":
            print(f"    {k}: {v}")


def cmd_forget(config: dict, index: int | None) -> None:
    """List persona facts, or remove one by index.

    Usage:
        seedling.py forget          # list persona facts with indices
        seedling.py forget 1        # remove the fact at index 1, then persist
    """
    from mcm import MCM
    import storage
    mcm = MCM(
        adapter_version=config.get("adapter_version", 0),
        base_model=config.get("base_model", "llama3.2"),
    )
    mcm.restore_context()
    state = mcm.current_state()
    facts = state.persona.facts if state else []
    if not facts:
        print("No persona facts stored.")
        return
    if index is None:
        print("Persona facts (use 'forget <index>' to remove one):")
        for i, f in enumerate(facts):
            print(f"  [{i}] ({f.kind} x{f.reinforce_count}) {f.text}")
        return
    if index < 0 or index >= len(facts):
        print(f"Index {index} out of range (0–{len(facts)-1}).")
        sys.exit(1)
    removed = facts.pop(index)
    storage.save_context_state(state)
    print(f"Forgotten: ({removed.kind}) {removed.text}")


def cmd_eval(config: dict) -> None:
    """Run evaluation report and failure mode tests."""
    import storage
    from eval import run_eval_report, test_failure_modes
    storage.init_db()
    deltas = storage.load_all_deltas()
    run_eval_report(deltas, config)
    test_failure_modes()


def cmd_tune(config: dict, approve: bool = False) -> None:
    """Show RDST scoring table. With --approve-tuning: run LoRA update."""
    import storage
    from tuner import score_threads, build_training_data, trigger_tuning
    from schemas import TuningJob
    import uuid

    storage.init_db()
    deltas = storage.load_all_deltas()

    if not deltas:
        print("No thread deltas found. Run some sessions first.")
        return

    scored = score_threads(
        deltas,
        correction_penalty=config.get("correction_penalty", 0.15),
        recency_decay_factor=config.get("recency_decay_factor", 0.05),
    )

    print(f"\nRDST Scoring — {len(scored)} threads\n")
    print(f"{'Thread ID':<36}  {'Raw':>5}  {'Wt.':>5}  {'Age':>4}  {'Emg':>4}")
    print("-" * 62)
    for st in scored:
        print(
            f"{st.delta.thread_id}  "
            f"{st.raw_score:>5.2f}  "
            f"{st.weighted_score:>5.3f}  "
            f"{st.age_in_threads:>4}  "
            f"{'Y' if st.delta.emergent else 'N':>4}"
        )

    if not approve:
        print("\nTo run LoRA tuning: python seedling.py tune --approve-tuning")
        print("This will modify your adapter. Review the scoring table above first.")
        return

    # Build training data
    job_id = str(uuid.uuid4())[:8]
    top_n = config.get("top_n_training", 10)
    training_path = build_training_data(scored, top_n=top_n, job_id=job_id)

    version_in = config.get("adapter_version", 0)
    version_out = version_in + 1

    composite = sum(st.weighted_score for st in scored[:top_n]) / min(top_n, len(scored))

    job = TuningJob(
        job_id=job_id,
        thread_ids_used=[st.delta.thread_id for st in scored[:top_n]],
        adapter_version_in=version_in,
        adapter_version_out=version_out,
        approved=True,     # set only because user passed --approve-tuning
        composite_signal=composite,
        status="approved",
    )

    model_path = input(f"\nEnter path to MLX-converted model (not GGUF): ").strip()
    if not model_path:
        print("No model path provided — aborting.")
        return

    trigger_tuning(job, model_path=model_path)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _apply_model_override(config: dict, args: list[str]) -> list[str]:
    """If '--model NAME' (or '--model=NAME') is present, override BOTH the chat
    model and the critic base model, then return args with the flag removed.
    A single --model is the one knob for 'which brain runs this session'; split
    them in config.yaml only when you deliberately want a different critic."""
    model = None
    cleaned = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--model" and i + 1 < len(args):
            model = args[i + 1]; i += 2; continue
        if a.startswith("--model="):
            model = a.split("=", 1)[1]; i += 1; continue
        cleaned.append(a); i += 1
    if model:
        config["model_name"] = model
        config["base_model"] = model
        print(ui.dim(f"[model override: chat + critic = {model}]"))
    return cleaned


def cmd_bench(config: dict, runs: int = 3) -> None:
    """Measure responsiveness against the live model: time-to-first-token (TTFT)
    and tokens/sec, averaged over N runs in an ISOLATED temp DB (never touches
    real memory). This is the evidence for whether a tuning change helps -- run
    it before and after editing chat_options in config.yaml."""
    import time, tempfile, shutil
    from pathlib import Path
    import storage
    from mcm import MCM
    from critic import CriticInstance
    from session import ThreadSession

    model = config.get("model_name", "llama3.2")
    base_model = config.get("base_model", model)
    opts = _chat_options_from_config(config)
    print(f"=== Seedling bench ===  model={model}  chat_options={opts or '(defaults)'}  runs={runs}")

    tmp = Path(tempfile.mkdtemp(prefix="seedling_bench_"))
    storage._DB_PATH = tmp / "db"; storage._db = None
    import session as S
    S._BUFFER_DIR = tmp / "buf"; S._BUFFER_DIR.mkdir(parents=True, exist_ok=True)
    prompts = [
        "In one sentence, what is the Second Arrow?",
        "Briefly, why does Gödel's incompleteness encourage humility?",
        "Give one reason local-first AI can be valuable.",
    ]
    ttfts, rates = [], []
    try:
        mcm = MCM(adapter_version=config.get("adapter_version", 0), base_model=base_model)
        critic = CriticInstance(backend=config.get("critic_backend", "local"), base_model=base_model)
        sess = ThreadSession(mcm=mcm, critic=critic, model_name=model, fresh=True,
                             deliberation_enabled=False, live_deliberation_enabled=False,
                             chat_options=opts)
        sess.start(); sess.warmup()
        for i in range(runs):
            first = {"t": None}; n = {"tok": 0}
            t0 = time.monotonic()
            def on_tok(tok, first=first, n=n, t0=t0):
                if first["t"] is None:
                    first["t"] = time.monotonic() - t0
                n["tok"] += 1
            out = sess.chat(prompts[i % len(prompts)], on_token=on_tok)
            total = time.monotonic() - t0
            sess._join_critic(timeout=60)
            ttft = first["t"] or total
            gen = max(1e-6, total - ttft)
            rate = n["tok"] / gen
            ttfts.append(ttft); rates.append(rate)
            print(f"  run {i+1}: ttft={ttft:.2f}s  full={total:.2f}s  ~{n['tok']} tok  ~{rate:.1f} tok/s")
        avg = lambda xs: sum(xs) / len(xs)
        print(f"\n  AVG ttft={avg(ttfts):.2f}s   AVG gen-rate={avg(rates):.1f} tok/s")
        print("  (Lower TTFT = snappier first response. Compare before/after a config change.)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True); storage._db = None


def main() -> None:
    config = _load_config()
    _setup_logging(config.get("log_level", "INFO"))

    args = sys.argv[1:]

    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return

    # --model NAME overrides chat + critic for this run (any subcommand).
    args = _apply_model_override(config, args)
    if not args:
        # they passed only --model with no subcommand: default to chat
        args = ["chat"]

    command = args[0]

    if command == "chat":
        fresh = "--fresh" in args
        cmd_chat(config, fresh=fresh)

    elif command == "snapshot":
        cmd_snapshot(config)

    elif command == "status":
        cmd_status(config)

    elif command == "forget":
        idx = None
        for a in args[1:]:
            if a.isdigit():
                idx = int(a)
                break
        cmd_forget(config, idx)

    elif command == "eval":
        cmd_eval(config)

    elif command == "bench":
        runs = 3
        for a in args[1:]:
            if a.isdigit():
                runs = int(a)
        cmd_bench(config, runs=runs)

    elif command == "tune":
        approve = "--approve-tuning" in args
        cmd_tune(config, approve=approve)

    else:
        print(f"Unknown command: {command}")
        print("Run: python seedling.py --help")
        sys.exit(1)


if __name__ == "__main__":
    main()
