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
from llm import create_backend_from_config, get_default_backend, set_default_backend
import inputsafe
import voicelayer

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


class _PhonemizerNoiseFilter(logging.Filter):
    """Drop phonemizer/espeak's cosmetic 'words count mismatch' warnings from a
    handler. These fire inside Kokoro TTS on punctuation/short lines; audio still
    plays and reasoning/memory are unaffected. Surgical: only the count-mismatch
    WARNING (and below) is dropped, so any genuine phonemizer ERROR still shows.
    Belt-and-suspenders alongside propagate=False (handles any future path where
    the record still reaches this handler)."""

    def filter(self, record: logging.LogRecord) -> bool:
        name = record.name or ""
        if name.startswith("phonemizer") or name.startswith("espeak"):
            if record.levelno <= logging.WARNING and "count mismatch" in record.getMessage():
                return False
        return True


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
    console.addFilter(_PhonemizerNoiseFilter())   # drop cosmetic TTS word-count noise
    root.addHandler(console)

    # httpx logs one INFO line per request -- always keep that off the console
    # (it lands mid-conversation). It still reaches the file via the root logger.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    # phonemizer/espeak (inside kokoro TTS) emit a cosmetic "words count mismatch"
    # WARNING on lines with punctuation/short input. Audio still plays and nothing
    # about reasoning/memory is affected — it's pure noise mid-conversation.
    #
    # Quieting it by level alone does NOT stick: phonemizer.logger.get_logger()
    # runs lazily on the first Kokoro synth (AFTER this setup) and unconditionally
    # does `logger.setLevel(WARNING)`, clobbering our ERROR. It then reaches the
    # screen by PROPAGATING to our root console handler. get_logger() never
    # touches `propagate`, so turning propagation off here is durable: the warning
    # can no longer bubble up to our handlers regardless of when the library
    # re-inits its own logger. A defensive filter is added to the console handler
    # below as belt-and-suspenders.
    for _noisy in ("phonemizer", "espeak"):
        _lg = logging.getLogger(_noisy)
        _lg.setLevel(logging.ERROR)
        _lg.propagate = False


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

def _normalize_model_command(user_input: str) -> str:
    """Accept ':models' as a friendly alias for ':model'."""
    s = user_input.strip()
    low = s.lower()
    if low == ":models":
        return ":model"
    if low.startswith(":models "):
        return ":model " + s[8:].lstrip()
    return s


def _handle_help_command() -> None:
    """In-chat command reference (discoverability hub)."""
    lines = [
        "Commands (single line only — pasted blocks are never commands):",
        "",
        "  :help              this list",
        "  :setup             backend, model, and connection status",
        "  :dispositions      your structural preferences (policy, not emotion)",
        "  :model             list models on the active backend",
        "  :model 2           switch by number from the list",
        "  :model <name>      switch by exact model id/tag",
        "  :read <path>       attach a local file or list a directory",
        "  :more              next chunk of a large attached file",
        "  :voice             voice on/off status",
        "  :voice on|off      toggle spoken replies",
        "  :voice chatty|terse|normal   how much she speaks aloud",
        "  exit / quit        end the session",
        "",
        "Model switches apply to THIS session only (chat + critic).",
        "To change the permanent default, edit model_name in config.yaml.",
        "To change backend (Ollama vs LM Studio), edit inference_backend",
        "in config.yaml and restart.",
    ]
    for line in lines:
        print("  " + (ui.dim(line) if line else ""))
    print()


def _handle_setup_command(session, config: dict) -> None:
    """Show inference stack status and actionable fixes for common mistakes."""
    llm = getattr(session, "llm", None) or get_default_backend()
    ok, detail = llm.probe()
    installed = llm.list_models()
    default_model = config.get("model_name", "llama3.2")
    backend = llm.friendly_name()
    status = ui.colored("OK", "32") if ok else ui.warn("NOT REACHABLE")

    print(f"  {ui.dim('── Inference setup ──')}")
    print(f"  Backend:   {backend} ({llm.name})")
    if llm.name == "openai_compat":
        print(f"  Server:    {getattr(llm, 'base_url', '')}")
    print(f"  Model:     {session.model_name}  (config default: {default_model})")
    print(f"  Status:    {status} — {detail}")
    if installed:
        print(f"  Available: {len(installed)} model{'s' if len(installed) != 1 else ''} "
              f"({', '.join(installed[:3])}{'…' if len(installed) > 3 else ''})")
    else:
        print("  Available: (could not list any — see tips below)")
    print()
    if llm.name == "ollama":
        print("  " + ui.dim("Tip: :model lists and switches. Missing models auto-pull."))
        if not ok:
            print("  " + ui.warn("Fix: run  ollama serve  or  bash run.sh"))
        elif session.model_name not in installed and installed:
            print("  " + ui.warn(
                f"Fix: current model '{session.model_name}' is not installed — "
                f"type  :model  to pick one, or  ollama pull {session.model_name}"
            ))
    else:
        print("  " + ui.dim(
            "Tip: load a model in your server UI first, then  :model  to pick it."
        ))
        if not ok:
            print("  " + ui.warn(
                "Fix: start your local server (e.g. LM Studio), then check "
                "openai_compat_base_url in config.yaml"
            ))
        elif session.model_name not in installed and installed:
            print("  " + ui.warn(
                f"Fix: '{session.model_name}' is not loaded on the server — "
                f"type  :model  to pick a loaded id"
            ))
        elif not installed:
            print("  " + ui.warn("Fix: load a model in your server, then  :model"))
    print()


def _startup_inference_check(session, config: dict) -> None:
    """Best-effort preflight — warn on problems but never block chat."""
    llm = getattr(session, "llm", None) or get_default_backend()
    ok, detail = llm.probe()
    if ok:
        if session.model_name not in llm.list_models() and llm.list_models():
            print("  " + ui.warn(
                f"Model '{session.model_name}' is not on the server — "
                f"type  :setup  or  :model  to fix before chatting."
            ))
        return
    print("  " + ui.warn(f"Inference server not ready: {detail}"))
    print("  " + ui.dim("Type  :setup  for details. Chat may fail until the server is up.\n"))


def _handle_dispositions_command(session, voice_prefs: dict | None = None) -> None:
    """Show Aida's structural preferences (policy sense, not emotions)."""
    import dispositions
    state = session.mcm.current_state()
    style = state.cognitive_style if state else None
    priors = state.persistent_priors if state else None
    tc = len(state.thread_deltas) if state else 0
    vp = voice_prefs or {}
    disps = dispositions.compute_dispositions(
        cognitive_style=style,
        persistent_priors=priors,
        speak_bias=getattr(session, "speak_bias", False),
        caution_enabled=getattr(session, "caution_controller_enabled", True),
        deliberation_enabled=getattr(session, "deliberation_enabled", True),
        voice_enabled=bool(vp.get("enabled")),
        voice_verbosity=vp.get("verbosity", "normal"),
        thread_count=tc,
    )
    for line in dispositions.render_dispositions_status(disps).splitlines():
        print("  " + ui.dim(line))
    print()


def _handle_model_command(session, user_input: str) -> None:
    """Handle the in-chat ':model' command (ephemeral switch for this session).

    Bare ':model'            -> list installed models (numbered, current marked).
    ':model <name>'          -> switch to that exact tag (auto-pulls if missing).
    ':model <number>'        -> switch to the Nth model from the listing.
    ':models'                -> alias for ':model'.
    """
    llm = getattr(session, "llm", None) or get_default_backend()
    arg = user_input[len(":model"):].strip()
    installed = llm.list_models()
    backend = llm.friendly_name()

    if not arg:
        ok, detail = llm.probe()
        if not ok:
            print("  " + ui.warn(f"[{backend} not reachable: {detail}]") + "\n")
            print("  " + ui.dim("Type  :setup  for fix steps.\n"))
            return
        if not installed:
            hint = (
                "Switch by exact id: :model <name>"
                if llm.name == "openai_compat"
                else "Switch by exact tag: :model qwen2.5:7b"
            )
            print("  " + ui.dim(f"[No models listed on {backend}. {hint}]") + "\n")
            return
        pull_note = "auto-pulls if missing" if llm.supports_pull() else "load in server UI first"
        print("  " + ui.dim(
            f"{backend} models (':model <number>' or ':model <name>' — {pull_note}):"
        ))
        for i, name in enumerate(installed, 1):
            mark = "  <- current" if name == session.model_name else ""
            print("    " + ui.dim(f"{i}. {name}{mark}"))
        print("  " + ui.dim("(Session only — edit config.yaml for a permanent default.)\n"))
        return

    # Numeric choice resolves against the listing.
    target = arg
    if arg.isdigit() and installed:
        idx = int(arg)
        if 1 <= idx <= len(installed):
            target = installed[idx - 1]
        else:
            print("  " + ui.dim(f"[No model #{idx}. There are {len(installed)} listed. Type ':model'.]\n"))
            return

    ok_probe, probe_detail = llm.probe()
    if not ok_probe:
        print("  " + ui.warn(f"[{backend} not reachable: {probe_detail}]") + "\n")
        print("  " + ui.dim("Type  :setup  for fix steps.\n"))
        return

    needs_pull = bool(installed) and target not in installed and llm.supports_pull()
    if not needs_pull and installed and target not in installed:
        print("  " + ui.warn(
            f"Model '{target}' is not in the server list — switching anyway. "
            f"If chat fails, load it in {backend} or pick with  :model"
        ))
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


def _session_caution_band(session) -> int:
    """Current caution band for voice gating (0=OFF if unknown)."""
    rep = getattr(session, "_last_caution_report", None)
    if rep is None:
        return 0
    band = getattr(rep, "band", 0)
    return int(band) if band is not None else 0


def _dispatch_voice_after_reply(
    response: str,
    session,
    voice_prefs: dict,
    read_state: dict,
    *,
    voice_speak,
) -> None:
    """Post-stream voice: speak immediately on the final text, then log notes.

    Hardened #10: TTS dispatches before dim audit lines so audio overlaps the
    moment the user is reading — always on the locked final response string."""
    if not voice_prefs.get("enabled") or response.startswith("[memory"):
        return
    spoken, note = voicelayer.route(
        response,
        voice_prefs,
        from_read=bool(read_state.get("text")),
        speak_bias=getattr(session, "speak_bias", False),
        lead_sentences=getattr(session, "speak_lead_sentences", 1),
        caution_band=_session_caution_band(session),
    )
    if spoken:
        voice_speak(spoken)
        voice_prefs["speak_count"] = voice_prefs.get("speak_count", 0) + 1
        voice_prefs["_last_kind"] = voicelayer.classify_kind(spoken)
    if note:
        print("  " + ui.dim(note))
    if spoken and not voice_prefs.get("_reminded"):
        voice_prefs["_reminded"] = True
        print("  " + ui.dim("[that was Aida speaking — say \"go silent\" "
                             "or type ':voice off' to mute]"))


def _stream_turn(session, turn_text: str, *,
                 voice_prefs: dict | None = None,
                 read_state: dict | None = None,
                 voice_speak=None) -> None:
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
    if voice_speak and voice_prefs:
        _dispatch_voice_after_reply(
            response, session, voice_prefs, read_state or {}, voice_speak=voice_speak)


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


def _handle_read_command(session, user_input: str, config: dict, read_state: dict,
                         *, voice_prefs: dict | None = None,
                         voice_speak=None) -> None:
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
    ok, name_or_err, text = filereader.load_path(path, max_mb=config.get("max_attach_mb"))
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
        if question:
            # One-shot: the user asked something up front — answer now (unchanged).
            print("  " + ui.dim(f"[attached {name} — CSV summary]"))
            _stream_turn(session, block + ask, voice_prefs=voice_prefs,
                         read_state=read_state, voice_speak=voice_speak)
        else:
            # Attach only: stage the summary and AWAIT the user's question, so
            # she never answers before they've said what they want.
            read_state.update({"name": name, "text": text, "done": True,
                               "staged": [block]})
            print("  " + ui.dim(f"[attached {name} — CSV summary. Ask a question about "
                                 "it, or press Enter for a quick orientation.]"))
        return

    if filereader.is_directory_listing(name):
        block = filereader.format_directory_block(text, name)
        read_state.clear()
        if question:
            print("  " + ui.dim(f"[attached {name}]"))
            _stream_turn(session, block + ask, voice_prefs=voice_prefs,
                         read_state=read_state, voice_speak=voice_speak)
        else:
            read_state.update({"name": name, "text": text, "done": True,
                               "staged": [block]})
            print("  " + ui.dim(f"[attached {name}. Ask a question about it, "
                                 "or press Enter for a quick orientation.]"))
        return

    budget = filereader.budget_chars(_config_num_ctx(config))
    chunk = filereader.read_chunk(text, name, char_offset=0, budget=budget)
    # Cache the full decoded text + where we are, so :more pages from memory.
    read_state.clear()
    read_state.update({"name": name, "text": text, "offset": chunk["next_offset"],
                       "total": chunk["total"], "budget": budget, "done": chunk["done"],
                       "staged": [chunk["block"]]})
    if question:
        # One-shot: the user asked up front — answer on chunk 1 now (unchanged).
        # Paging state is kept so ':more' still works afterward if they want it.
        tail = "" if chunk["done"] else " (':more' for the next part)"
        print("  " + ui.dim(f"[attached {name} — chunk {chunk['chunk_no']}{tail}]"))
        read_state["staged"] = []          # consumed by this immediate answer
        _stream_turn(session, chunk["block"] + ask, voice_prefs=voice_prefs,
                     read_state=read_state, voice_speak=voice_speak)
    else:
        # Attach only: STAGE the chunk and do NOT call the model. This is the fix
        # for "Aida answers before I can type ':more'" — she now waits until the
        # user pages what they want and asks.
        tail = (" (':more' for the next part, or ask a question about it)"
                if not chunk["done"] else
                " (ask a question about it, or press Enter for a quick orientation)")
        print("  " + ui.dim(f"[attached {name} — chunk {chunk['chunk_no']}{tail}]"))


def _compose_staged_turn(read_state: dict, user_input: str) -> tuple[str, bool]:
    """Fold any STAGED file chunks (from ':read'/':more' with no up-front question)
    into this turn. Pure + deterministic so it's unit-testable.

    Returns (turn_text, submit):
      * submit=False  -> nothing to send this loop (caller should skip).
      * submit=True   -> send turn_text to the model.
    When staged content is present it is consumed (cleared) here. An empty
    user_input WITH staged content is a valid "respond now" signal (generic
    orientation); an empty user_input with nothing staged is a no-op.
    """
    staged = (read_state or {}).get("staged") or []
    if not staged:
        return user_input, bool(user_input)   # normal turn (skip if empty)
    fname = (read_state or {}).get("name", "the attached file")
    partial = "" if (read_state or {}).get("done", True) else \
        " (partial view; more of the file was not shown)"
    if user_input:
        ask = f"The user attached {fname}{partial} (shown above) and asks: {user_input}"
    else:
        ask = (f"The user attached {fname}{partial} (shown above). Briefly say what it is "
               "and what you can help with; then await their question.")
    turn = "\n\n".join(staged) + "\n\n" + ask
    read_state["staged"] = []   # consumed
    return turn, True


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
    # STAGE the chunk (do NOT call the model): the user pages through the whole
    # file at their pace, then Aida answers once they ask (or press Enter). This
    # is the fix for her replying before ':more' could be typed.
    read_state.setdefault("staged", []).append(chunk["block"])
    tail = (" (':more' for more, or ask a question about it)" if not chunk["done"] else
            " (end of file — ask a question about it, or press Enter for a quick orientation)")
    print("  " + ui.dim(f"[{read_state['name']} — chunk {chunk['chunk_no']}{tail}]"))


def cmd_chat(config: dict, fresh: bool = False) -> None:
    """Start an interactive chat session."""
    from mcm import MCM
    from critic import CriticInstance
    from session import ThreadSession

    llm = create_backend_from_config(config)
    set_default_backend(llm)

    mcm = MCM(
        adapter_version=config.get("adapter_version", 0),
        base_model=config.get("base_model", "llama3.2"),
    )
    critic = CriticInstance(
        backend=config.get("critic_backend", "local"),
        base_model=config.get("base_model", "llama3.2"),
        perplexity_model=config.get("perplexity_model", "sonar"),
        llm=llm,
    )
    session = ThreadSession(
        mcm=mcm,
        critic=critic,
        model_name=config.get("model_name", "llama3.2"),
        fresh=fresh,
        llm=llm,
        tuning_threshold_n=config.get("tuning_threshold_n", 10),
        deliberation_enabled=config.get("deliberation_enabled", True),
        live_deliberation_enabled=config.get("live_deliberation_enabled", True),
        history_window_turns=config.get("history_window_turns", 24),
        live_annotation_enabled=config.get("live_annotation_enabled", False),
        chat_options=_chat_options_from_config(config),
        deliberation_drain_timeout_s=config.get("deliberation_drain_timeout_s", 90.0),
        collaborative_wall_enabled=config.get("collaborative_wall_enabled", False),
        wall_act_cutoff=config.get("wall_act_cutoff", 0.70),
        wall_coherence_floor=config.get("wall_coherence_floor", 0.30),
        wall_coherence_ceiling=config.get("wall_coherence_ceiling", 0.65),
        wall_balance_margin=config.get("wall_balance_margin", 0.30),
        wall_gate_cutoff=config.get("wall_gate_cutoff", 0.50),
        wall_gate_cooldown_turns=config.get("wall_gate_cooldown_turns", 3),
        wall_gate_max_per_session=config.get("wall_gate_max_per_session", 3),
        speak_bias=config.get("speak_bias", False),
        speak_lead_sentences=config.get("speak_lead_sentences", 1),
        caution_controller_enabled=config.get("caution_controller_enabled", True),
        caution_integral_half_life=config.get("caution_integral_half_life", 3.0),
        caution_wall_session_cap=config.get("caution_wall_session_cap", 0.65),
    )

    print("\n" + "="*60)
    print("  SEEDLING — Local AI Continuity Runtime")
    print("="*60)
    backend_label = llm.friendly_name()
    if llm.name == "openai_compat":
        backend_label = f"{backend_label} ({getattr(llm, 'base_url', '')})"
    print(f"  {backend_label}  |  model: {config.get('model_name', 'llama3.2')}")
    if not os.environ.get("PERPLEXITY_API_KEY") and config.get("critic_backend") == "perplexity":
        print("  ⚠  PERPLEXITY_API_KEY not set — critic will fall back to local")
        print("  Set it with: export PERPLEXITY_API_KEY=pplx-...")
    print()

    context_injection = session.start()
    # Warm the model now (one tiny generation) so the FIRST real turn doesn't pay
    # the cold-load cost. Best-effort; failures are ignored.
    session.warmup()
    _startup_inference_check(session, config)
    if fresh:
        print("[Fresh session — no prior context]\n")
    else:
        print("[Context restored]\n")

    print("Type  :help  for commands  |  :setup  for model & backend status")
    print(ui.dim("(Paste multiple lines = one turn. Commands are single-line only.)\n"))
    read_state: dict = {}   # paging state for the currently-attached file (:read/:more)
    # Voice layer prefs. Voice is ON BY DEFAULT when macOS `say` is available so
    # a new user simply HEARS Aida; it can be turned off in plain language
    # ("go silent") or with :voice off. Explicit opt-out wins:
    #   AIDA_VOICE=0  or  voice_enabled: false  -> force off.
    _voice_prefs = voicelayer.default_prefs()
    _env_voice = os.environ.get("AIDA_VOICE")
    _cfg_voice = config.get("voice_enabled", None)
    # TTS engine + voice from config. Engine "kokoro" prefers the local neural
    # voice and auto-falls back to macOS `say`; "say" uses the built-in directly.
    # These are threaded through to voicelayer.speak so the choice is honored
    # everywhere, without moving or altering any floor/eligibility/mute gate.
    _tts_engine = str(config.get("tts_engine", "say")).strip().lower()
    _tts_voice = config.get("tts_voice", None)
    _kokoro_model_path = config.get("kokoro_model_path", voicelayer.DEFAULT_KOKORO_MODEL)
    _kokoro_voices_path = config.get("kokoro_voices_path", voicelayer.DEFAULT_KOKORO_VOICES)
    _voice_prefs["engine"] = _tts_engine
    _voice_prefs["voice"] = _tts_voice
    _voice_prefs["model_path"] = _kokoro_model_path
    _voice_prefs["voices_path"] = _kokoro_voices_path

    def _voice_speak(text: str) -> bool:
        """Speak via the configured engine (kokoro->say fallback handled in
        voicelayer). Centralizes engine/voice/paths so every call site is
        consistent. Additive only — never affects the printed reply."""
        return voicelayer.speak(
            text, voice=_tts_voice, engine=_tts_engine,
            model_path=_kokoro_model_path, voices_path=_kokoro_voices_path)

    def _voice_available() -> bool:
        return voicelayer.voice_available(
            _tts_engine, _kokoro_model_path, _kokoro_voices_path)

    _voice_prefs["_was_available"] = _voice_available()
    if _env_voice == "0" or _cfg_voice is False:
        _voice_prefs["enabled"] = False
    else:
        _voice_prefs["enabled"] = _voice_available()
    _voice_prefs["_reminded"] = False
    if _voice_prefs["enabled"]:
        print("Aida will SPEAK her short replies aloud. To silence her, just say "
              "\"go silent\" (or type ':voice off'); say \"speak again\" to turn it back on.")
        print(ui.dim("  ':voice chatty' / ':voice terse' adjusts how much she speaks.\n"))
        if _tts_engine == "kokoro":
            voicelayer.prewarm_kokoro(_kokoro_model_path, _kokoro_voices_path)
    elif _env_voice == "1" and not _voice_available():
        print(ui.dim("  [voice: requested but no local speech engine available here "
                     "— staying text-only]\n"))

    try:
        while True:
            try:
                # Poka-yoke: while silenced, the prompt itself shows how to
                # resume — the way back is always on screen, never lost to scroll.
                _suffix = voicelayer.prompt_suffix(_voice_prefs)
                _prompt = ("You: " if not _suffix
                           else f"You:{ui.dim(_suffix)} ")
                raw = inputsafe.read_multiline(_prompt)
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
                if user_input.lower() in (":help", ":?"):
                    _handle_help_command()
                    continue
                if user_input.lower() == ":setup":
                    _handle_setup_command(session, config)
                    continue
                if user_input.lower() == ":dispositions":
                    _handle_dispositions_command(session, _voice_prefs)
                    continue
                user_input = _normalize_model_command(user_input)
                if user_input.lower() == ":model" or user_input.lower().startswith(":model "):
                    _handle_model_command(session, user_input)
                    continue
                # In-chat file attach. The RUNTIME reads a user-named local file
                # and feeds its real contents in as the turn — the model never
                # reaches files on its own (the guard still forbids that).
                if user_input.lower() == ":read" or user_input.lower().startswith(":read "):
                    _handle_read_command(session, user_input, config, read_state,
                                         voice_prefs=_voice_prefs, voice_speak=_voice_speak)
                    continue
                # ':more' pages forward through the currently-attached file.
                if user_input.lower() == ":more":
                    _handle_more_command(session, read_state)
                    continue
                # Voice: teachable mute + on/off. ':quiet' mutes the KIND Aida
                # last spoke (your plain-language correction; learning only ever
                # silences). ':voice on|off' toggles the whole layer.
                if user_input.lower() == ":quiet":
                    last = _voice_prefs.get("_last_kind")
                    if last:
                        voicelayer.teach_mute(_voice_prefs, last)
                        print("  " + ui.dim(f"[voice: won't speak '{last}' aloud anymore]"))
                    else:
                        print("  " + ui.dim("[voice: nothing spoken yet to quiet]"))
                    continue
                # Bare ':voice' = status + how to change it (cheap escape hatch).
                if user_input.lower() == ":voice":
                    if _voice_prefs.get("enabled"):
                        verb = voicelayer.verbosity_label(_voice_prefs)
                        print("  " + ui.dim(
                            f"[voice: ON — verbosity {verb}. "
                            f"':voice chatty|terse|normal' to adjust; "
                            f"\"go silent\" or ':voice off' to mute]"
                        ))
                    elif _voice_available():
                        print("  " + ui.dim("[voice: OFF — say \"speak again\" or ':voice on' to resume]"))
                    else:
                        print("  " + ui.dim("[voice: unavailable (no local speech engine) — text-only]"))
                    continue
                if user_input.lower() in (":voice chatty", ":voice terse", ":voice normal"):
                    mode = user_input.rsplit(maxsplit=1)[-1].lower()
                    _voice_prefs["verbosity"] = mode
                    print("  " + ui.dim(
                        f"[voice: verbosity set to {mode}"
                        + (" — speaks more (incl. 2 lead sentences on long replies)"
                           if mode == "chatty" else
                           " — short pleasantries only, no lead sentences on long replies"
                           if mode == "terse" else
                           " — default speak amount")
                        + "]"
                    ))
                    continue
                if user_input.lower() in (":voice off", ":voice on"):
                    if user_input.lower().endswith("on") and _voice_available():
                        _voice_prefs["enabled"] = True
                        _voice_speak(voicelayer.RESUME_CONFIRM)   # spoken feedback
                        print("  " + ui.dim("[voice: on]"))
                    else:
                        _voice_prefs["enabled"] = False
                        print("  " + ui.dim("[voice: off]"))
                    continue
                # Natural-language voice toggle: turn speech off/on by SAYING so.
                # Deterministic + conservative (whole-message imperative only),
                # runs BEFORE the model so it always works and never reaches the
                # LLM as a chat turn. Confirmed in text either way.
                _intent = voicelayer.detect_voice_intent(user_input)
                if _intent == "silence":
                    _voice_prefs["enabled"] = False
                    print("  " + ui.dim("[voice: off — I'll stay quiet. Say \"speak again\" "
                                         "anytime to turn it back on.]"))
                    continue
                if _intent == "speak":
                    if _voice_available():
                        _voice_prefs["enabled"] = True
                        # Poka-yoke #5: speak a one-time confirmation so the user
                        # gets sensory proof the resume worked, not just text.
                        _voice_speak(voicelayer.RESUME_CONFIRM)
                        print("  " + ui.dim("[voice: on — I'll speak my short replies aloud. "
                                             "Say \"go silent\" to stop.]"))
                    else:
                        print("  " + ui.dim("[voice: no local speech engine here — staying text-only]"))
                    continue
                # Plain-language local read/list: route to the :read runtime when
                # the user names a local path (deterministic; never URLs/GitHub).
                import filereader as _fr
                _read_intent = _fr.detect_local_read_intent(user_input)
                if _read_intent:
                    _path, _q = _read_intent
                    _cmd = ":read " + _path + (f" {_q}" if _q else "")
                    print("  " + ui.dim(f"[reading local path: {_path}]"))
                    _handle_read_command(session, _cmd, config, read_state,
                                         voice_prefs=_voice_prefs, voice_speak=_voice_speak)
                    continue
            # Fold any file chunks the user staged with ':read'/':more' into this
            # turn (so paging no longer triggers an early reply). An empty line is
            # a valid "respond now" only when something is staged.
            turn_text, _submit = _compose_staged_turn(read_state, user_input)
            if not _submit:
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
                response = session.chat(turn_text, on_token=_on_token)
            finally:
                _spinner.stop()                     # also clears the [memory]/no-stream paths

            if response.startswith("[memory"):
                # No tokens were streamed; render the confirmation line.
                print("\n" + ui.dim(response) + "\n")
            else:
                if _stream_state["started"]:
                    print("\n")          # close the streamed line
                else:
                    print(f"{ui.reply_prefix_inline()}{response}\n")
                _dispatch_voice_after_reply(
                    response, session, _voice_prefs, read_state,
                    voice_speak=_voice_speak)
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

                # --- COLLABORATIVE WALL (opt-in, RARE, synchronous) ---
                # When enabled AND this turn's deliberation genuinely hit a wall,
                # Aida surfaces her lean as a QUESTION and folds the answer back
                # through the EXISTING belief friction (never an auto-promote).
                # Most turns do nothing and return immediately. Interactive only
                # (needs a real stdin to ask); skipped on piped input.
                if (getattr(session, "collaborative_wall_enabled", False)
                        and sys.stdin.isatty()):
                    def _ask_at_wall(q: str) -> str:
                        print("\n  " + ui.dim("⌟ Aida hit a wall and wants your read:"))
                        print("  " + q.replace("\n", "\n  "))
                        try:
                            return input("  You: ")
                        except (EOFError, KeyboardInterrupt):
                            return ""
                    try:
                        session.collaborative_wall(user_input, response, _ask_at_wall)
                        for notice in getattr(session, "_memory_notices", []):
                            if "wall" in notice:
                                print("  " + ui.dim(notice))
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

    llm = create_backend_from_config(config)
    set_default_backend(llm)
    model = config.get("model_name", "llama3.2")
    base_model = config.get("base_model", model)
    opts = _chat_options_from_config(config)
    print(f"=== Seedling bench ===  backend={llm.name}  model={model}  chat_options={opts or '(defaults)'}  runs={runs}")

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
        critic = CriticInstance(
            backend=config.get("critic_backend", "local"), base_model=base_model, llm=llm
        )
        sess = ThreadSession(
            mcm=mcm, critic=critic, model_name=model, fresh=True,
            deliberation_enabled=False, live_deliberation_enabled=False,
            chat_options=opts, llm=llm,
        )
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
    set_default_backend(create_backend_from_config(config))

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
