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
import localconfig
import replcmds
import voicelayer

logger = logging.getLogger(__name__)

_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE))


def _load_config() -> dict:
    """Shipped defaults from config.yaml, with config.local.yaml layered on top.

    config.yaml is tracked and carries the comments explaining every trade-off;
    config.local.yaml is gitignored and holds only this machine's deltas. Every
    runtime write goes to the local file, so using Aida cannot dirty the repo.
    See localconfig for the merge rules.
    """
    config_path = _HERE / "config.yaml"
    if config_path.exists() or localconfig.local_path(config_path).exists():
        return localconfig.load(config_path)
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


def _stdin_is_interactive() -> bool:
    try:
        return bool(sys.stdin.isatty())
    except Exception:
        return False


def _dispatch_colon_command(
    user_input: str,
    *,
    session,
    config: dict,
    voice_prefs: dict,
    voice_speak,
    voice_available,
    read_state: dict,
    read_pick_state: dict,
) -> bool:
    """Run a single-line colon command. True if the line was consumed.

    Unknown/typo `:verb` lines are intercepted here and never sent as chat.
    Lines that are not command-shaped return False so the caller may offer a
    missing-colon rewrite or fall through to Aida.
    """
    low = user_input.lower()
    if low in (":help", ":?"):
        _handle_help_command()
        return True
    if low == ":learning":
        _handle_learning_command()
        return True
    if low == ":setup":
        _handle_setup_command(session, config)
        return True
    if low == ":status":
        _handle_status_command(session, config)
        return True
    if low == ":dispositions":
        _handle_dispositions_command(session, voice_prefs)
        return True
    if low.startswith(":forget-doc"):
        arg = user_input[len(":forget-doc"):].strip()
        if not arg:
            print(ui.dim("  Usage: :forget-doc <file name as attached>  "
                         "(or an 8-hex provenance hash)"))
            return True
        import re as _re
        from session import _doc_hash as _dh
        h = arg.lower() if _re.fullmatch(r"[0-9a-f]{8}", arg.lower()) else _dh(arg)
        moved = session.mcm.quarantine_source(f"document:{h}")
        if moved:
            print(ui.dim(f"  Quarantined {len(moved)} belief(s) from "
                         f"document:{h} (archived, revivable):"))
            for b in moved:
                print(ui.dim(f"    - {b.text[:70]}"))
        else:
            print(ui.dim(f"  No active beliefs carry document:{h} provenance."))
        return True
    if low == ":reflect":
        if not config.get("reflection_enabled", True):
            print(ui.dim("  Reflection is disabled (reflection_enabled: false)."))
            return True
        from reflection import run_reflection
        rep = run_reflection(
            session,
            max_deliberations=config.get("reflection_max_deliberations", 1))
        print(rep.render())
        return True
    _tune_cmd = inputsafe.normalize_repl_input(user_input).strip().lower()
    if _tune_cmd == ":tune" or _tune_cmd.startswith(":tune "):
        _dispatch_tune_command(session, config, user_input)
        return True
    user_input = _normalize_model_command(user_input)
    low = user_input.lower()
    if low == ":model" or low.startswith(":model "):
        _handle_model_command(session, user_input)
        return True
    if low == ":read" or low.startswith(":read "):
        _handle_read_command(session, user_input, config, read_state,
                             voice_prefs=voice_prefs, voice_speak=voice_speak,
                             read_pick_state=read_pick_state)
        return True
    if low == ":search" or low.startswith(":search "):
        _handle_search_command(session, user_input, config, read_state,
                               voice_prefs=voice_prefs, voice_speak=voice_speak)
        return True
    if low == ":scan" or low.startswith(":scan "):
        _handle_scan_command(config, user_input, session=session)
        return True
    if low == ":allow" or low.startswith(":allow "):
        _handle_allow_command(config, user_input)
        return True
    if low == ":enable" or low.startswith(":enable "):
        _handle_enable_command(config, user_input, turn_on=True, session=session)
        return True
    if low == ":disable" or low.startswith(":disable "):
        _handle_enable_command(config, user_input, turn_on=False, session=session)
        return True
    if low in (":capabilities", ":caps"):
        _handle_capabilities_command(config)
        return True
    if low == ":more":
        _handle_more_command(session, read_state)
        return True
    if low == ":quiet":
        last = voice_prefs.get("_last_kind")
        if last:
            voicelayer.teach_mute(voice_prefs, last)
            print("  " + ui.dim(f"[voice: won't speak '{last}' aloud anymore]"))
        else:
            print("  " + ui.dim("[voice: nothing spoken yet to quiet]"))
        return True
    if low == ":voice":
        if voice_prefs.get("enabled"):
            verb = voicelayer.verbosity_label(voice_prefs)
            print("  " + ui.dim(
                f"[voice: ON — verbosity {verb}. "
                f"':voice chatty|terse|normal' to adjust; "
                f"\"go silent\" or ':voice off' to mute]"
            ))
        elif voice_available():
            print("  " + ui.dim("[voice: OFF — say \"speak again\" or ':voice on' to resume]"))
        else:
            print("  " + ui.dim("[voice: unavailable (no local speech engine) — text-only]"))
        return True
    if low in (":voice chatty", ":voice terse", ":voice normal"):
        mode = user_input.rsplit(maxsplit=1)[-1].lower()
        voice_prefs["verbosity"] = mode
        print("  " + ui.dim(
            f"[voice: verbosity set to {mode}"
            + (" — speaks more (incl. 2 lead sentences on long replies)"
               if mode == "chatty" else
               " — short pleasantries only, no lead sentences on long replies"
               if mode == "terse" else
               " — default speak amount")
            + "]"
        ))
        return True
    if low in (":voice off", ":voice on"):
        if low.endswith("on") and voice_available():
            voice_prefs["enabled"] = True
            voice_speak(voicelayer.RESUME_CONFIRM)
            print("  " + ui.dim("[voice: on]"))
        else:
            voice_prefs["enabled"] = False
            print("  " + ui.dim("[voice: off]"))
        return True
    if low == ":theme" or low.startswith(":theme ") or low.startswith(":theme:"):
        _handle_theme_command(user_input, config)
        return True
    _colon_note = replcmds.colon_fallthrough_notice(user_input)
    if _colon_note:
        print("  " + ui.dim(_colon_note) + "\n")
        return True
    return False


def _handle_help_command() -> None:
    """In-chat command reference (discoverability hub)."""
    from learning_ui import format_learning_commands_lines, format_learning_tiers_lines
    from search_intent import format_help_lines

    lines = [
        "Commands (single line only — pasted blocks are never commands):",
        "A leading : is a command. A typo is not sent as chat.",
        "Missing : is offered as a command first; n sends the line to Aida.",
        "Aida may quietly offer :read/:search/:more when you named a path; y/ok runs it.",
        "",
        "  :help              this list",
        "  :status            quick health (chat input, inference, learning)",
        "  :setup             backend, model, chat input, and attachment readers",
        "  :dispositions      your structural preferences (policy, not emotion)",
        "  :model             list models on the active backend",
        "  :model 2           switch by number from the list",
        "  :model <name>      switch by exact model id/tag",
        "  :read <path>       attach a local file, PDF, DOCX, or list a directory",
        *format_help_lines(),
        "  :scan              secret/IP scan (opt-in; optional: <path>)",
        "  :allow             list search folders; :allow <path> / :allow drop N",
        "  :capabilities      list gated flags and their state (read-only listing)",
        "  :enable <flag>     turn on a capability gate: search / scan (:disable too)",
        "  :more              next chunk of a large attached file",
        "                     (after a bad :read path: reply  y/1  or a number)",
        "  :reflect           sleep pass: review archived beliefs + old insights",
        "  :forget-doc <file> retract beliefs learned from an attached document",
        "  :voice             voice on/off status",
        "  :voice on|off      toggle spoken replies",
        "  :voice chatty|terse|normal   how much she speaks aloud",
        "  :theme             console theme — on now, kept next session",
        "  :theme dark|light-color|b&w  apply instantly; last choice persists",
        *format_learning_commands_lines(),
        "  exit / quit        end the session",
        "",
        *format_learning_tiers_lines(expanded=False),
        "",
        "Model switches apply to THIS session only (chat + critic).",
        "Theme is kept across sessions (last :theme writes config.local.yaml).",
        "To change the permanent default model, edit model_name in config.yaml.",
        "To change backend (Ollama vs LM Studio), edit inference_backend",
        "in config.yaml and restart.",
    ]
    for line in lines:
        print("  " + (ui.dim(line) if line else ""))
    print()


def _handle_learning_command() -> None:
    """Expanded Tier 1 vs Tier 2 guide (read-only)."""
    from learning_ui import format_learning_tiers_lines

    for line in format_learning_tiers_lines(expanded=True):
        print("  " + (ui.dim(line) if line else ""))
    print()


def _mlx_lora_readiness() -> tuple[bool, str]:
    """Return (ready, detail) for optional deep LoRA tuning via mlx-lm."""
    try:
        import mlx_lm  # noqa: F401
    except ImportError:
        return False, "mlx-lm not installed (Apple Silicon only — pip install mlx-lm)"
    except Exception as e:
        return False, f"mlx-lm check failed ({type(e).__name__})"
    return True, "mlx-lm installed"


def _handle_tune_status_command(session, config: dict) -> None:
    """Read-only learning status: Tier 1 + Tier 2 readiness. Never raises."""
    from tuning_facade import (
        adapter_artifact_status,
        last_tuning_job_summary_safe,
        session_learning_counts,
    )

    thread_count, threshold, adapter_version, err = session_learning_counts(session, config)
    tuning_ready = thread_count >= threshold

    print(f"  {ui.dim('── Learning status ──')}")
    if err:
        print("  " + ui.warn(err))
    print("  Tier 1 (auto)    : active — reasoning style updates every session")
    print(f"  Sessions         : {thread_count} / {threshold} captured")
    if tuning_ready:
        print("  Tier 2 (opt-in)  : threshold reached")
    else:
        remaining = max(0, threshold - thread_count)
        print(f"  Tier 2 (opt-in)  : {remaining} more session(s) needed")

    print(f"  Adapter version  : v{adapter_version}"
          + (" (base model)" if adapter_version == 0 else ""))

    mlx_ok, mlx_detail = _mlx_lora_readiness()
    if adapter_version > 0:
        print(f"  LoRA artifact    : {adapter_artifact_status(adapter_version)}")
    elif tuning_ready:
        print(f"  LoRA tooling     : {mlx_detail}")

    last_job = last_tuning_job_summary_safe()
    if last_job:
        print(f"  Tuning history   : {last_job}")

    print()
    print("  " + ui.dim(
        "Tier 1 (automatic) is already shaping every reply."
    ))
    if tuning_ready:
        if mlx_ok:
            print("  " + ui.dim(
                "Tier 2 preview:  :tune preview  (full guide:  :learning)"
            ))
            print("  " + ui.dim(
                "Run (gate must PASS):  python seedling.py tune --approve-tuning"
            ))
        else:
            print("  " + ui.dim(f"Tier 2 needs: {mlx_detail}"))
    print("  " + ui.dim(
        "Tier 2 weights are not loaded in chat yet — Tier 1 is the live path."
    ))
    print()


def _score_deltas_for_tune(config: dict):
    """Load thread deltas and return (deltas, scored) for RDST preview/approve."""
    from tuning_facade import score_deltas_safe

    deltas, scored, err = score_deltas_safe(config)
    if err:
        print("\n  " + ui.warn(err) + "\n")
        return [], []
    return deltas, scored


def _print_tune_preview(config: dict) -> bool:
    """Print RDST preview + eval gate. Returns True if deltas exist. Never raises."""
    from eval import format_tuning_gate_lines, format_approve_path_lines
    from tuning_facade import assess_gate_safe, coerce_tuning_params, score_deltas_safe
    from tuner import estimate_training_stats, format_scoring_table, format_training_preview_lines

    try:
        params = coerce_tuning_params(config)
        deltas, scored, err = score_deltas_safe(config)
        if err:
            print("\n  " + ui.warn(err) + "\n")
            return False
        if not deltas:
            print("\nNo thread deltas found. Run some sessions first.\n")
            return False

        top_n = params["top_n_training"]
        version_in = params["adapter_version"]
        version_out = version_in + 1
        stats = estimate_training_stats(scored, top_n=top_n)
        if stats.get("error"):
            print("  " + ui.warn(f"Training preview error: {stats['error']}"))

        gate, gate_err = assess_gate_safe(
            deltas, config, training_stats=stats
        )
        if gate_err or gate is None:
            print("\n  " + ui.warn(gate_err or "Eval gate unavailable.") + "\n")
            return True

        for line in format_scoring_table(scored):
            print(line)
        for line in format_training_preview_lines(stats, version_in=version_in, version_out=version_out):
            print(line)
        for line in format_tuning_gate_lines(gate):
            print(line)

        mlx_ok, mlx_detail = _mlx_lora_readiness()
        for line in format_approve_path_lines(gate, mlx_ok=mlx_ok, mlx_detail=mlx_detail):
            print(line)
        print()
        return True
    except Exception as e:
        logger.exception("_print_tune_preview failed")
        print("\n  " + ui.warn(f"Tune preview failed ({type(e).__name__}). See logs/seedling.log.") + "\n")
        return False


def _handle_tune_preview_command(config: dict) -> None:
    """In-chat RDST preview + eval gate (read-only)."""
    print(f"  {ui.dim('── Tier 2 preview (read-only) ──')}")
    _print_tune_preview(config)


def _dispatch_tune_command(session, config: dict, user_input: str) -> None:
    """Pole-yoke router for all :tune subcommands. Never raises."""
    from tuning_facade import parse_tune_subcommand

    sub = parse_tune_subcommand(user_input)
    if sub == "status":
        _handle_tune_status_command(session, config)
    elif sub == "preview":
        _handle_tune_preview_command(config)
    else:
        print("  " + ui.dim("Unknown :tune command. Try  :tune status  or  :tune preview"))
        print("  " + ui.dim("Full guide:  :learning\n"))


def _handle_setup_command(session, config: dict) -> None:
    """Show inference, chat input, and attachment-reader status with fix tips."""
    from docxreader import format_attachment_readers_status_lines

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
    for line in inputsafe.format_readline_status_lines():
        if line.startswith("  Fix") or line.startswith("  Then"):
            print("  " + ui.warn(line.strip()))
        elif "NEEDS FIX" in line:
            print("  " + ui.warn(line.strip()))
        else:
            print(line)
    print()
    for line in format_attachment_readers_status_lines(config):
        if "NEEDS FIX" in line:
            print("  " + ui.warn(line.strip()))
        else:
            print(line if line.startswith("  ") else "  " + line)
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


def _handle_status_command(session, config: dict) -> None:
    """Quick health: chat input, inference, learning — read-only."""
    from tuning_facade import session_learning_counts

    print(f"  {ui.dim('── Status ──')}")

    for line in inputsafe.format_readline_status_lines():
        if "NEEDS FIX" in line or line.startswith("  Fix") or line.startswith("  Then"):
            print("  " + ui.warn(line.strip()))
        else:
            print(line)

    llm = getattr(session, "llm", None) or get_default_backend()
    ok, detail = llm.probe()
    inf = ui.colored("OK", "32") if ok else ui.warn("NOT REACHABLE")
    print(f"  Inference      : {inf} — {session.model_name} ({llm.friendly_name()})")
    if not ok:
        print("  " + ui.warn(f"Fix inference: {detail} — type  :setup  for steps"))

    from docxreader import format_attachment_readers_status_lines
    for line in format_attachment_readers_status_lines(config):
        if "NEEDS FIX" in line:
            print("  " + ui.warn(line.strip()))
        else:
            print(line if line.startswith("  ") else "  " + line)

    tc, thresh, _, err = session_learning_counts(session, config)
    if err:
        print("  " + ui.warn(f"Learning       : {err}"))
    else:
        print(f"  Learning       : {tc} / {thresh} sessions —  :tune status  for Tier 1/2")
    print("  " + ui.dim("Details:  :setup  (model)  |  :tune status  (learning)  |  :learning  (guide)"))
    print()


def _startup_terminal_check() -> None:
    """Warn once at chat start if line editing is degraded on this platform."""
    st = inputsafe.readline_editing_status()
    if st["ok"] or not st.get("fix_command"):
        return
    print("  " + ui.warn(st["detail"]))
    print("  " + ui.warn(f"Fix: {st['fix_command']}"))
    if st.get("fix_note"):
        print("  " + ui.dim(st["fix_note"]))
    print("  " + ui.dim("Or type  :status  anytime for the full health check.\n"))


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
            sys.stdout.write("\r  " + ui.dim(f"{_label(status)}\u2026") + "   ")
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
    moment the user is reading — always on the locked final response string.
    Turn weight (light greeting vs substantive) shapes speak preference only;
    the floor still decides hard silence.
    """
    if not voice_prefs.get("enabled") or response.startswith("[memory"):
        return
    last_user = ""
    try:
        for m in reversed(getattr(session, "_messages", []) or []):
            if m.get("role") == "user":
                last_user = m.get("content") or ""
                break
    except Exception:
        last_user = ""
    try:
        import voice as _voice
        turn_weight = _voice.classify_turn_weight(last_user)
    except Exception:
        turn_weight = "standard"
    spoken, note = voicelayer.route(
        replcmds.strip_offers(response),
        voice_prefs,
        from_read=bool(read_state.get("text")),
        speak_bias=getattr(session, "speak_bias", False),
        lead_sentences=getattr(session, "speak_lead_sentences", 1),
        caution_band=_session_caution_band(session),
        turn_weight=turn_weight,
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
    _writer: ui.ReplyStreamWriter | None = None

    def _on_token(tok: str) -> None:
        nonlocal _writer
        if _writer is None:
            _spinner.stop()
            _writer = ui.ReplyStreamWriter()
            _state["started"] = True
        _writer.feed(tok)

    try:
        response = session.chat(turn_text, on_token=_on_token)
    finally:
        _spinner.stop()
    if _writer is not None:
        _writer.finish()
        print("\n")
    else:
        from session import strip_emergent_markers_for_display
        sys.stdout.write(ui.format_wrapped_reply(
            replcmds.strip_offers(
                strip_emergent_markers_for_display(response))))
        print("\n")
    if voice_speak and voice_prefs:
        _dispatch_voice_after_reply(
            response, session, voice_prefs, read_state or {}, voice_speak=voice_speak)
    _note_offer_from_reply(
        session, response,
        last_user=turn_text,
        has_attachment=bool((read_state or {}).get("text")),
    )


def _config_num_ctx(config: dict):
    """Pull num_ctx from chat_options if set, else None (Ollama default)."""
    opts = config.get("chat_options") or {}
    return opts.get("num_ctx")


def _nl_read_command(user_input: str) -> str | None:
    """Conversational local-read → ``:read <path> <ask>`` so THIS turn includes
    the file. Bare ``:read path`` (command channel) still stages and waits;
    asking Aida to read a named path must not leak to the model without bytes."""
    import filereader as fr
    return fr.conversational_read_command(user_input)


def _parse_read_arg(arg: str) -> tuple[str, str | None]:
    """Split ':read' argument into (path, optional_question).

    Delegates to filereader.parse_read_arg so spaced paths, globs, and trailing
    questions parse the same way as plain-language ``read ...`` requests.
    """
    import filereader
    return filereader.parse_read_arg(arg)


def _note_offer_from_reply(
    session, response: str, *,
    last_user: str = "",
    has_attachment: bool = False,
) -> None:
    """Parse at most one grounded [offer]. Never auto-runs. Quiet chrome."""
    import replcmds
    cmd = replcmds.parse_offer(response)
    if cmd and not replcmds.offer_fits_conversation(
        cmd, last_user, has_attachment=has_attachment,
    ):
        cmd = None
    session._pending_offer = cmd
    if not cmd:
        return
    print("  " + ui.dim(f"[offer {cmd}]"))


def _run_confirmed_offer(
    cmd: str, *,
    session, config: dict, read_state: dict, read_pick_state: dict,
    voice_prefs: dict | None = None, voice_speak=None,
) -> None:
    """Runtime-dispatch a user-confirmed offer. Whitelist is parse_offer's job."""
    import replcmds
    verb = replcmds.colon_verb(cmd)
    if verb == "read":
        arg = cmd[len(":read"):].strip()
        path, q = _parse_read_arg(arg)
        ask = q or (
            "The user confirmed your offer. Read the attached file and "
            "continue the conversation."
        )
        _handle_read_command(
            session, f":read {path} {ask}", config, read_state,
            voice_prefs=voice_prefs, voice_speak=voice_speak,
            read_pick_state=read_pick_state,
        )
        return
    if verb == "search":
        _handle_search_command(
            session, cmd, config, read_state,
            voice_prefs=voice_prefs, voice_speak=voice_speak,
        )
        return
    if verb == "more":
        _handle_more_command(session, read_state)
        if not (read_state or {}).get("staged"):
            return
        turn, submit = _compose_staged_turn(
            read_state,
            "The user confirmed :more. Review this next chunk and continue.",
        )
        if submit:
            _stream_turn(
                session, turn,
                voice_prefs=voice_prefs, read_state=read_state,
                voice_speak=voice_speak,
            )


def _try_pending_offer_turn(
    user_input: str, *,
    session, config: dict, read_state: dict, read_pick_state: dict,
    voice_prefs: dict | None = None, voice_speak=None,
) -> bool:
    """True if the line consumed a pending offer (confirm or decline)."""
    import replcmds
    pending = getattr(session, "_pending_offer", None)
    if not pending:
        return False
    kind = replcmds.offer_reply_kind(user_input)
    if kind == "decline":
        session._pending_offer = None
        return True
    if kind == "confirm":
        session._pending_offer = None
        _run_confirmed_offer(
            pending, session=session, config=config,
            read_state=read_state, read_pick_state=read_pick_state,
            voice_prefs=voice_prefs, voice_speak=voice_speak,
        )
        return True
    # Chat: expire silently so conversation does not require dismissing first.
    session._pending_offer = None
    return False



def _colon_arg(user_input: str, name: str) -> str:
    """Strip a leading :name regardless of case."""
    raw = (user_input or "").strip()
    prefix = ":" + name
    if raw.lower().startswith(prefix):
        return raw[len(prefix):].strip()
    return raw


def _ask_yes_no(prompt: str, *, default: bool = False) -> bool:
    """Ask once. Empty Enter uses `default`. Interrupt / EOF ⇒ False."""
    try:
        ans = inputsafe.normalize_repl_input(input(prompt)).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    if not ans:
        return default
    if ans in ("y", "yes"):
        return True
    if ans in ("n", "no"):
        return False
    return default


def _offer_enable_flag(
    config: dict,
    key: str,
    *,
    what: str,
    detail: str = "",
    config_path: Path | None = None,
    ask=None,
) -> bool:
    """A capability gate is off and the user just asked for it. Offer it here.

    Same shape as _ensure_named_roots_allowed, on purpose: state the situation,
    ask exactly once, write config.yaml for next time, apply to the live dict for
    this turn, and degrade to session-only if the file write fails. Returns the
    resulting state so the caller can carry on in the same turn.
    """
    import flags
    if bool(config.get(key)):
        return True
    if not flags.is_toggleable(key):
        print("  " + ui.dim(f"[{key}: {flags.GUARDED_REASON}.]") + "\n")
        return False
    cfg_path = config_path or (_HERE / "config.yaml")
    ask_fn = ask or _ask_yes_no
    print(f"  {what} is off." + (f" {detail}" if detail else ""))
    if not ask_fn(f"  Turn it on and write {key}: true to config.yaml? [y/N] "):
        print("  " + ui.dim(f"[left off — {key} stays false]") + "\n")
        return False
    ok, msg = flags.set_flag_local(cfg_path, key, True)
    flags.apply_flag_to_config(config, key, True)
    if ok:
        print("  " + ui.dim(f"[{msg} — active now, no restart needed]"))
    else:
        print("  " + ui.dim(f"[session only: {msg}]"))
    return True


def _ensure_named_roots_allowed(
    config: dict,
    roots: list[str] | None,
    *,
    offer: bool = True,
    config_path: Path | None = None,
    ask=None,
) -> bool:
    """If the user named a path that exists but isn't allowlisted, ask to add it."""
    if not roots:
        return True
    import rga_search
    cfg_path = config_path or (_HERE / "config.yaml")
    ask_fn = ask or _ask_yes_no
    allowed = rga_search.expand_allowed(list(config.get("rga_search_allowed_paths") or []))
    for raw in roots:
        try:
            p = rga_search.resolve_named_root(raw)
        except OSError as e:
            print("  " + ui.dim(f"[{e}]") + "\n")
            return False
        if not (p.is_dir() or p.is_file()):
            print("  " + ui.dim(f"[Named path does not exist: {p}]") + "\n")
            return False
        if rga_search.path_is_allowed(p, allowed):
            continue
        if not offer:
            continue
        print(f"  {p} is not on the search allowlist.")
        if not ask_fn("  Allow it and write it to config.yaml? [y/N] "):
            print("  " + ui.dim("[left off the allowlist — search stays inside the list]") + "\n")
            return False
        # Apply to the live dict first, then persist the RESULTING list to
        # config.local.yaml. Local lists replace rather than append, so the
        # whole effective list is written; the tracked config.yaml is never
        # touched, so a personal allowlist cannot dirty the repository.
        rga_search.apply_allowed_path_to_config(config, p)
        allowed = rga_search.expand_allowed(
            list(config.get("rga_search_allowed_paths") or [])
        )
        ok, msg = localconfig.set_key(
            cfg_path, "rga_search_allowed_paths",
            list(config.get("rga_search_allowed_paths") or []),
        )
        if ok:
            print("  " + ui.dim(f"[added {p} to rga_search_allowed_paths — {msg}]"))
        else:
            # The live dict already has it, so the named search still runs.
            print("  " + ui.dim(f"[session only: {msg}]"))
    return True


def _handle_allow_command(config: dict, user_input: str,
                          *, config_path: Path | None = None, ask=None) -> None:
    """List / add / drop rga_search_allowed_paths. Does not flip feature flags."""
    import rga_search
    action, rest = rga_search.parse_allow_arg(_colon_arg(user_input, "allow"))
    allowed = list(config.get("rga_search_allowed_paths") or [])
    cfg_path = config_path or (_HERE / "config.yaml")
    if action == "usage":
        print("  " + rga_search.format_allow_listing(allowed).replace("\n", "\n  "))
        print("  Usage: :allow <path>   or   :allow drop N\n")
        return
    if action == "list":
        print("  " + rga_search.format_allow_listing(allowed).replace("\n", "\n  ") + "\n")
        return
    if action == "add":
        if not _ensure_named_roots_allowed(
            config, [rest], config_path=cfg_path, ask=ask,
        ):
            return
        print("  " + rga_search.format_allow_listing(
            list(config.get("rga_search_allowed_paths") or []),
        ).replace("\n", "\n  ") + "\n")
        return
    # Resolve against the LIVE list, drop it there, then persist the resulting
    # list to config.local.yaml. config.yaml is never rewritten.
    token = rga_search.strip_wrapping_quotes(rest)
    target = None
    expanded = rga_search.expand_allowed(allowed)
    if token.isdigit() and 1 <= int(token) <= len(expanded):
        target = expanded[int(token) - 1]
    else:
        try:
            cand = rga_search.resolve_named_root(token)
        except OSError:
            cand = None
        if cand is not None:
            target = cand
    if target is None:
        print("  " + ui.dim(f"[no allowlist entry matches {rest!r}]"))
    else:
        rga_search.apply_drop_to_config(config, target)
        ok, msg = localconfig.set_key(
            cfg_path, "rga_search_allowed_paths",
            list(config.get("rga_search_allowed_paths") or []),
        )
        if ok:
            print("  " + ui.dim(f"[removed {target} from rga_search_allowed_paths]"))
        else:
            print("  " + ui.dim(f"[session only: {msg}]"))
    print("  " + rga_search.format_allow_listing(
        list(config.get("rga_search_allowed_paths") or []),
    ).replace("\n", "\n  ") + "\n")


def _search_chat_fn(session):
    """Stateless interpreter/fit calls. Never session.chat."""
    if session is None or not hasattr(session, "_chat_once"):
        return None

    def chat_fn(messages, options):
        try:
            return session._chat_once(
                session.model_name, messages, options=options, think=False,
            )
        except Exception:
            return session._chat_once(
                session.model_name, messages, options=options,
            )
    return chat_fn


def _execute_spec_search(spec, *, enabled, allowed, config, max_hits=None, timeout_s=None):
    import rga_search
    extra_pats = spec.patterns[1:] if len(spec.patterns) > 1 else None
    return rga_search.run_search(
        spec.pattern,
        enabled=enabled,
        allowed_paths=allowed,
        roots=spec.roots,
        max_hits=rga_search.coerce_max_hits(
            max_hits if max_hits is not None else config.get("rga_search_max_hits")
        ),
        timeout_s=rga_search.coerce_timeout_s(
            timeout_s if timeout_s is not None else config.get("rga_search_timeout_s")
        ),
        max_filesize=rga_search.coerce_max_filesize(config.get("rga_search_max_filesize")),
        no_cache=False,
        exact=spec.exact,
        case=spec.case,
        max_depth=spec.depth,
        match_kind=spec.match_kind,
        extra_patterns=extra_pats,
    )


def _maybe_retry_mismatched_search(
    spec, result, *, session, enabled, allowed, config, ask,
):
    """One English retry: fit check, smoke, y/N. Never widens. Never loops."""
    import rga_search
    import search_intent as si
    chat_fn = _search_chat_fn(session)
    if chat_fn is None or not spec.interpreted:
        return spec, result
    fit, try_ask = si.judge_search_fit(spec, result.hits, chat_fn=chat_fn)
    if fit or not try_ask:
        return spec, result
    retry = si.spec_from_try(spec, try_ask)
    if retry is None:
        return spec, result
    if retry.needs_interpret:
        retry = si.interpret_search_spec(retry, chat_fn=chat_fn)
    try:
        smoke = _execute_spec_search(
            retry, enabled=enabled, allowed=allowed, config=config,
            max_hits=si.SMOKE_MAX_HITS, timeout_s=si.SMOKE_TIMEOUT_S,
        )
    except rga_search.SearchDenied:
        return spec, result
    if not smoke.hits:
        return spec, result
    print("  " + ui.dim(si.format_did_you_mean(
        try_ask, first_n=len(result.hits), smoke_n=len(smoke.hits),
    )))
    ask_fn = ask or _ask_yes_no
    if not ask_fn(f"  Search {try_ask!r} instead? [y/N] "):
        print("  " + ui.dim("[keeping the first search]") + "\n")
        return spec, result
    try:
        second = _execute_spec_search(
            retry, enabled=enabled, allowed=allowed, config=config,
        )
    except rga_search.SearchDenied as e:
        print("  " + ui.dim(f"[{e}]") + "\n")
        return spec, result
    retry.interpret_note = (
        (retry.interpret_note + "; " if retry.interpret_note else "")
        + f"retried from {spec.original!r}"
    )[:160]
    return retry, second


def _handle_search_command(session, user_input: str, config: dict, read_state: dict,
                           *, config_path: Path | None = None, ask=None,
                           voice_prefs: dict | None = None, voice_speak=None) -> None:
    """Handle ':search …' — gated search, then Aida reviews the hits."""
    import rga_search
    import search_intent as si
    arg = _colon_arg(user_input, "search")
    enabled = bool(config.get("rga_search_enabled"))
    allowed = list(config.get("rga_search_allowed_paths") or [])
    spec = si.parse_search_spec(arg)
    if not spec.pattern:
        if spec.file_only:
            print("  Need something to look for in that file.  "
                  "Example: :search retry /path/file.txt")
        elif spec.roots:
            print("  Need something to look for in that folder.  "
                  "Example: :search retry in /path")
        print("  " + rga_search.format_search_usage(
            enabled=enabled, allowed_paths=allowed,
        ).replace("\n", "\n  ") + "\n")
        return
    if not enabled:
        enabled = _offer_enable_flag(
            config, "rga_search_enabled",
            what="Corpus search",
            detail="Searches only the folders you allowlist; the runtime reads, "
                   "the model never browses.",
            config_path=config_path, ask=ask,
        )
        if not enabled:
            return
    if not _ensure_named_roots_allowed(
        config, spec.roots, offer=enabled, config_path=config_path, ask=ask,
    ):
        return
    allowed = list(config.get("rga_search_allowed_paths") or [])
    chat_fn = _search_chat_fn(session)
    if spec.needs_interpret:
        if chat_fn is not None:
            print("  " + ui.dim("[interpreting search…]"))
        spec = si.interpret_search_spec(spec, chat_fn=chat_fn)
    try:
        result = _execute_spec_search(
            spec, enabled=enabled, allowed=allowed, config=config,
        )
    except rga_search.SearchDenied as e:
        print("  " + ui.dim(f"[{e}]") + "\n")
        return
    spec, result = _maybe_retry_mismatched_search(
        spec, result, session=session, enabled=enabled, allowed=allowed,
        config=config, ask=ask,
    )
    how = spec.summary()
    preamble = f"How: {how}"
    n = len(result.hits)
    extra_l = " (truncated)" if result.truncated else ""
    print("  " + ui.dim(f"[search: {n} hit(s){extra_l} — {how}]") + "\n")
    if not result.hits and session is None:
        print("  " + ui.dim(f"[{result.message or 'no matching content found'}]") + "\n")
        return
    block = rga_search.format_search_block(result, preamble=preamble)
    ask_txt = si.format_search_ask(spec=spec)
    read_state.clear()
    read_state["kind"] = "search"
    read_state["name"] = "search results"
    read_state["text"] = block
    read_state["done"] = True
    read_state["offset"] = len(block)
    read_state["budget"] = len(block)
    read_state["chunk_no"] = 1
    read_state["search_spec"] = spec
    if session is not None:
        read_state["staged"] = []
        _stream_turn(session, block + "\n" + ask_txt,
                     voice_prefs=voice_prefs, read_state=read_state,
                     voice_speak=voice_speak)
        return
    read_state["staged"] = [block]


def _scan_summary_for_model(findings: list, message: str, config: dict) -> str:
    """Build the content-free line the model is allowed to know about a scan.

    THE PRIVACY RULE LIVES HERE and is unit-tested in test_local_actions.py:
    never a path, never an excerpt, never matched text. The default says only
    that a scan ran and that its output stayed in the terminal, so the
    documented "nothing is sent to the model" remains literally true.

    Setting scan_summary_to_model: true opts into aggregate counts and finding
    KINDS (e.g. "12 ipv4_literal") — still no paths and no matched text. That is
    off by default because it is a real, if small, widening of what leaves the
    terminal, and that should be the user's explicit choice rather than a
    default they inherit from an upgrade.
    """
    n = len(findings or [])
    # The default is deliberately neutral about the OUTCOME too. Saying "no
    # findings" vs "findings were shown" would still disclose one bit about the
    # user's disk, and "nothing is sent to the model" is printed verbatim in the
    # report itself — so the default keeps that literally true and reveals only
    # that the command ran.
    base = (
        ":scan ran and printed its report in the user's terminal. The results, "
        "including whether anything was found, were not shared with you."
    )
    if not bool((config or {}).get("scan_summary_to_model")):
        return base
    kinds: dict[str, int] = {}
    for f in findings or []:
        kinds[getattr(f, "kind", "unknown")] = kinds.get(getattr(f, "kind", "unknown"), 0) + 1
    if not kinds:
        return base
    breakdown = ", ".join(f"{c} {k}" for k, c in sorted(kinds.items(), key=lambda kv: -kv[1]))
    return (
        f":scan ran: {n} finding(s) by kind ({breakdown}). Paths and matched "
        "text stayed in the terminal and were not given to you."
    )


def _resolve_scan_roots(roots: list[str] | None,
                       allowed: list[str] | None) -> tuple[list[str] | None, bool]:
    """Make a named scan root concrete, or explain why it cannot be.

    Returns (roots, ok). Never guesses between two possibilities: an ambiguous
    name is listed back for the user to pick, because the alternative is reading
    a folder they did not mean.
    """
    import rga_search
    if not roots:
        return roots, True
    out: list[str] = []
    for raw in roots:
        try:
            direct = rga_search.resolve_named_root(raw)
        except OSError:
            direct = None
        if direct is not None and (direct.is_dir() or direct.is_file()):
            out.append(str(direct))
            continue
        cands = rga_search.resolve_scan_candidates(raw, allowed)
        if len(cands) == 1:
            print("  " + ui.dim(f'[scoping to {cands[0]} — resolved from "{raw}"]'))
            out.append(str(cands[0]))
            continue
        if len(cands) > 1:
            print(f'  "{raw}" could be more than one place:')
            for i, c in enumerate(cands, 1):
                print(f"    {i}. {c}")
            print("  " + ui.dim("[name one exactly: :scan <path>]") + "\n")
            return roots, False
        print("  " + ui.dim(f"[Named path does not exist: {raw}]") + "\n")
        return roots, False
    return out, True


def _handle_scan_command(config: dict, user_input: str = ":scan",
                         *, config_path: Path | None = None, ask=None,
                         session=None) -> None:
    """Handle ':scan' — gated read-only secret/IP scan. Never staged into the model."""
    import security_scan
    from rga_search import SearchDenied
    enabled = bool(config.get("security_scan_enabled"))
    allowed = list(config.get("rga_search_allowed_paths") or [])
    roots, err = security_scan.parse_scan_arg(_colon_arg(user_input, "scan"))
    if err:
        print("  " + security_scan.format_scan_usage(
            enabled=enabled, allowed_paths=allowed,
        ).replace("\n", "\n  ") + "\n")
        return
    # A loosely-typed root ("myfolder/") is resolved before anything else, so a
    # typo does not first prompt to enable a capability it will never use.
    roots, resolved_ok = _resolve_scan_roots(roots, allowed)
    if not resolved_ok:
        return
    if not enabled:
        enabled = _offer_enable_flag(
            config, "security_scan_enabled",
            what="Security scan",
            detail="Read-only secret/IP scan of allowlisted folders; "
                   "findings are never sent to the model.",
            config_path=config_path, ask=ask,
        )
        if not enabled:
            return
    if not _ensure_named_roots_allowed(
        config, roots, offer=enabled, config_path=config_path, ask=ask,
    ):
        return
    allowed = list(config.get("rga_search_allowed_paths") or [])
    try:
        findings, msg = security_scan.run_scan(
            enabled=enabled,
            allowed_paths=allowed,
            roots=roots,
        )
    except SearchDenied as e:
        print("  " + ui.dim(f"[{e}]") + "\n")
        return
    print("  " + security_scan.format_scan_report(findings, msg).replace("\n", "\n  "))
    print()
    # The report above never enters the transcript. Record that it RAN so a
    # follow-up like "which of those are false positives?" gets an honest "that
    # output wasn't shared with me" instead of being resolved against the
    # system prompt. Content-free by construction.
    if session is not None:
        try:
            session.note_local_action(
                _scan_summary_for_model(findings, msg, config)
            )
        except Exception as e:
            logger.error(f"scan activity note skipped: {e}")


def _handle_capabilities_command(config: dict) -> None:
    """Handle ':capabilities' — read-only flag listing."""
    import capabilities
    print("  " + capabilities.format_listing(config).replace("\n", "\n  "))
    print()


def _handle_theme_command(user_input: str, config: dict,
                          config_path: Path | None = None) -> None:
    """Handle ':theme' / ':theme dark|light-color|b&w' — visual only.

    Live immediately (this session). Last successful choice is the next
    session's starting theme (config.local.yaml). Unknown names are refused,
    not guessed. No restart.
    """
    raw = user_input[len(":theme"):].strip() if user_input.lower().startswith(":theme") else ""
    if raw.startswith(":"):
        raw = raw[1:]
    if not raw:
        print("  " + ui.dim(
            f"[theme: {ui.current_theme()} — on now, kept for the next session. "
            f"Change anytime: :theme dark | :theme light-color | :theme b&w]"
        ) + "\n")
        return
    name = ui.parse_theme(raw)
    if not name:
        print("  " + ui.dim(
            "[unknown theme — use  dark  |  light-color  |  b&w]"
        ) + "\n")
        return
    ui.set_theme(name)
    ui.apply_canvas()
    config["theme"] = name
    cfg_path = config_path or (_HERE / "config.yaml")
    ok, msg = localconfig.set_key(cfg_path, "theme", name)
    if ok:
        print("  " + ui.dim(
            f"[theme: {name} — on now, kept for next session]"
        ) + "\n")
    else:
        print("  " + ui.dim(
            f"[theme: {name} — on now for this session only ({msg})]"
        ) + "\n")


def _handle_enable_command(config: dict, user_input: str, *, turn_on: bool,
                           config_path: Path | None = None, session=None) -> None:
    """Handle ':enable <flag>' / ':disable <flag>' — capability gates only.

    Integrity guards are refused here by design; see flags.CHAT_TOGGLEABLE.
    """
    import flags
    verb = "enable" if turn_on else "disable"
    raw = _colon_arg(user_input, verb)
    toggleable = ", ".join(sorted(flags.CHAT_TOGGLEABLE))
    if not raw.strip():
        print(f"  Usage: :{verb} <flag>       e.g.  :{verb} scan")
        print(f"  Toggleable from chat: {toggleable}")
        print("  " + ui.dim("[other flags stay in config.yaml on purpose — "
                            "honesty guards are not chat-settable]") + "\n")
        return
    key = flags.normalize_flag_name(raw)
    if not flags.is_toggleable(key):
        print("  " + ui.dim(f"[{key}: {flags.GUARDED_REASON}.]"))
        print("  " + ui.dim(f"[toggleable from chat: {toggleable}]") + "\n")
        return
    cfg_path = config_path or (_HERE / "config.yaml")
    ok, msg = flags.set_flag_local(cfg_path, key, turn_on)
    flags.apply_flag_to_config(config, key, turn_on)
    state = "on" if turn_on else "off"
    if session is not None:
        try:
            session.note_local_action(
                f":{'enable' if turn_on else 'disable'} {key} — that capability "
                f"is now {state}."
            )
        except Exception as e:
            logger.error(f"enable activity note skipped: {e}")
    if ok:
        print("  " + ui.dim(f"[{msg} — {key} is {state} now, no restart needed]") + "\n")
    elif "already" in msg:
        print("  " + ui.dim(f"[{msg} {key} is {state} for this session too.]") + "\n")
    else:
        print("  " + ui.dim(f"[session only: {msg}]") + "\n")


def _handle_read_command(session, user_input: str, config: dict, read_state: dict,
                         *, voice_prefs: dict | None = None,
                         voice_speak=None,
                         read_pick_state: dict | None = None) -> None:
    """Handle ':read <path>' — attach a local text/py/csv file as the turn.

    The runtime (filereader) reads the named file deterministically; its REAL
    contents are fed in as a normal graded turn. Large text/py files are shown in
    a context-budgeted CHUNK; ':more' pages forward. CSV is a structural summary
    (not paged). The model never reaches files on its own; every partial view
    carries an explicit paging notice so it can't characterize unseen content.
    """
    import filereader
    if read_pick_state is not None:
        read_pick_state.clear()
    arg = user_input[len(":read"):].strip()
    path, question = _parse_read_arg(arg)
    path, path_notice = filereader.resolve_read_path(path)
    if path_notice:
        print("  " + ui.dim(f"[{path_notice}]"))
    ok, name_or_err, text = filereader.load_path(
        path, max_mb=config.get("max_attach_mb"), pdf_options=config)
    if not ok:
        opts = filereader.read_suggest_options_from_config(config)
        candidates: list[str] = []
        # Miss menu only for true absence / empty glob — never binary, permission,
        # size, or decode refusals on a path that already exists.
        if (opts["enabled"] and path.strip()
                and filereader.should_offer_read_miss_menu(path)):
            candidates = filereader.rank_path_candidates(
                path,
                max_candidates=opts["max_candidates"],
                min_score=opts["min_score"],
            )
        if candidates and read_pick_state is not None:
            read_pick_state.update({
                "candidates": candidates,
                "attempted": path,
                "question": question,
            })
            menu = filereader.format_read_pick_menu(path, candidates)
            for line in menu.splitlines():
                print("  " + ui.warn(line))
            print()
            read_state.clear()
            return
        print("  " + ui.warn(name_or_err) + "\n")   # yellow: honest read error, no turn
        read_state.clear()
        return
    name = name_or_err

    # If the user appended a question/comment, ask it; else a generic orient prompt.
    # Citation grounding lives in _read_ask_suffix (same contract as staged turns).
    ask = "\n\n" + _read_ask_suffix(question=question, fname=name)

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
            read_state.update({
                "name": name, "text": text, "done": True,
                "kind": "csv", "staged": [block],
                "source_path": str(Path(path).expanduser().resolve()),
            })
            print("  " + ui.dim(f"[attached {name} — CSV summary. Ask a question about "
                                 "it, or press Enter for a quick orientation.]"))
        return

    if filereader.is_directory_listing(name):
        budget = filereader.budget_chars(_config_num_ctx(config))
        chunk = filereader.read_directory_chunk(
            text, name, char_offset=0, budget=budget)
        read_state.clear()
        read_state.update({
            "name": name, "text": text, "offset": chunk["next_offset"],
            "total": chunk["total"], "budget": budget, "done": chunk["done"],
            "chunk_no": chunk["chunk_no"],
            "kind": "directory", "staged": [chunk["block"]],
            # Keep the user-authorized directory as deterministic follow-up
            # context. The model still cannot read it; the runtime may reattach
            # one explicitly named direct child on a later turn.
            "source_path": str(Path(path).expanduser().resolve()),
        })
        if question:
            print("  " + ui.dim(
                f"[attached {name} — chunk {chunk['chunk_no']}"
                + ("" if chunk["done"] else " (':more' for the next part)")
                + "]"))
            read_state["staged"] = []
            _stream_turn(session, chunk["block"] + ask, voice_prefs=voice_prefs,
                         read_state=read_state, voice_speak=voice_speak)
            return

        # No question: offer a numbered browse menu (pick a path) OR Return to
        # review the staged listing. SNR: don't dump the model until they choose.
        ok_e, dir_path, entries = filereader.list_directory_entries(path)
        pick_cap = filereader.DEFAULT_MAX_DIR_PICK
        menu_entries = entries[:pick_cap] if ok_e else []
        candidates = [full for _label, full in menu_entries]
        if candidates and read_pick_state is not None:
            read_pick_state.clear()
            read_pick_state.update({
                "mode": "directory",
                "candidates": candidates,
                "labels": [label for label, _full in menu_entries],
                "attempted": path,
                "question": None,
            })
            menu = filereader.format_directory_browse_menu(
                dir_path if ok_e else path,
                menu_entries,
                total_count=len(entries) if ok_e else len(menu_entries),
            )
            for line in menu.splitlines():
                print("  " + ui.dim(line))
            page_hint = ("" if chunk["done"] else
                         " Listing is long — after Return, ':more' pages it.")
            print("  " + ui.dim(
                f"[directory ready — 1–{len(candidates)} opens a path; Return "
                f"reviews the listing; n cancels.{page_hint}]"))
            print()
            return

        # Empty dir / no pick state: fall back to staged listing only.
        tail = (" (':more' for the next part, or ask a question about it)"
                if not chunk["done"] else
                " (ask a question about it, or press Enter for a quick orientation)")
        print("  " + ui.dim(f"[attached {name} — chunk {chunk['chunk_no']}{tail}]"))
        return

    budget = filereader.budget_chars(_config_num_ctx(config))
    chunk = filereader.read_chunk(text, name, char_offset=0, budget=budget)
    # Cache the full decoded text + where we are, so :more pages from memory.
    read_state.clear()
    read_state.update({"name": name, "text": text, "offset": chunk["next_offset"],
                       "total": chunk["total"], "budget": budget, "done": chunk["done"],
                       "chunk_no": chunk["chunk_no"],
                       "kind": "file", "staged": [chunk["block"]],
                       "source_path": str(Path(path).expanduser().resolve())})
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


def _try_read_pick_turn(
    user_input: str,
    read_pick_state: dict,
    session,
    config: dict,
    read_state: dict,
    *,
    voice_prefs: dict | None = None,
    voice_speak=None,
) -> str:
    """Handle one input line while a :read disambiguation menu is active.

    Returns:
      'handled'  — pick/cancel/retry consumed; caller should continue the REPL loop
      'review_now' — directory Return; caller orients on staged listing
      'fallthrough' — not a pick; menu cleared; caller should process input normally
    """
    import filereader
    from pathlib import Path as _Path

    candidates = read_pick_state.get("candidates") or []
    if not candidates:
        return "fallthrough"
    mode = read_pick_state.get("mode") or "miss"
    labels = read_pick_state.get("labels") or []
    action, picked = filereader.parse_read_pick_response(
        user_input, candidates, mode=mode, labels=labels)

    if action == "retry":
        n = len(candidates)
        print("  " + ui.warn(
            f"Invalid choice — enter a number 1–{n}, a unique name from the list, "
            f"Return to review"
            + (" the listing" if mode == "directory" else "")
            + ", or n to cancel."))
        return "handled"

    if action == "pick" and picked:
        question = read_pick_state.get("question")
        target = picked.rstrip("/\\")
        # Fault tolerance: path may have vanished since the menu was drawn.
        if not _Path(os.path.expanduser(target)).exists():
            print("  " + ui.warn(
                f"That path is gone or unreadable ({target}). "
                "Menu still open — pick another number, Return, or n."))
            return "handled"

        # Snapshot so a failed open can restore the menu (poka-yoke).
        saved_pick = {
            "mode": mode,
            "candidates": list(candidates),
            "labels": list(labels),
            "attempted": read_pick_state.get("attempted"),
            "question": question,
        }
        saved_read = dict(read_state) if mode == "directory" else {}

        read_pick_state.clear()
        read_state.clear()
        cmd = ":read " + target + (f" {question}" if question else "")
        print("  " + ui.dim(f"[reading: {target}]"))
        _handle_read_command(
            session, cmd, config, read_state,
            voice_prefs=voice_prefs, voice_speak=voice_speak,
            read_pick_state=read_pick_state,
        )
        # Failed open + no new menu → restore prior menu, but drop the picked
        # path when it still exists (binary/perm/size). Re-offering it loops.
        opened = bool(read_state.get("text") or read_pick_state.get("candidates"))
        if not opened and saved_pick.get("candidates"):
            remaining = filereader.drop_existing_pick_candidate(
                list(saved_pick["candidates"]), target)
            if remaining:
                read_pick_state.clear()
                restored = {k: v for k, v in saved_pick.items() if v is not None}
                restored["candidates"] = remaining
                if restored.get("labels"):
                    old_c = saved_pick.get("candidates") or []
                    old_l = saved_pick.get("labels") or []
                    label_map = {
                        old_c[i]: old_l[i]
                        for i in range(min(len(old_c), len(old_l)))
                    }
                    aligned = [label_map[c] for c in remaining if c in label_map]
                    if len(aligned) == len(remaining):
                        restored["labels"] = aligned
                    else:
                        restored.pop("labels", None)
                read_pick_state.update(restored)
                if saved_read:
                    read_state.clear()
                    read_state.update(saved_read)
                print("  " + ui.warn(
                    "Couldn't open that path — it was removed from the menu."))
                # Removing a candidate changes every later number. Always redraw
                # so the visible menu and parser cannot silently disagree.
                if mode == "directory":
                    restored_labels = restored.get("labels") or [
                        Path(c.rstrip("/\\")).name for c in remaining
                    ]
                    menu = filereader.format_directory_browse_menu(
                        restored.get("attempted") or "",
                        list(zip(restored_labels, remaining)),
                        total_count=len(remaining),
                    )
                else:
                    menu = filereader.format_read_pick_menu(
                        restored.get("attempted") or "", remaining)
                for line in menu.splitlines():
                    print("  " + ui.dim(line))
                print()
            else:
                read_pick_state.clear()
        elif opened and saved_read.get("source_path") and read_state.get("kind") != "directory":
            # Preserve sibling follow-up context after selecting a file.
            read_state["browse_directory"] = saved_read["source_path"]
        return "handled"

    if action == "review":
        if mode == "directory" and not (read_state.get("staged") or read_state.get("text")):
            read_pick_state.clear()
            print("  " + ui.warn(
                "Nothing staged to review — directory state was lost. "
                "Re-run :read <dir>."))
            return "handled"
        read_pick_state.clear()
        print("  " + ui.dim(
            "[reviewing directory listing with Aida"
            + ("" if read_state.get("done", True) else " — :more pages further after this")
            + "]"))
        return "review_now"

    if action == "cancel":
        read_pick_state.clear()
        if mode == "directory":
            read_state.clear()
        print("  " + ui.dim("[read pick cancelled]") + "\n")
        return "handled"

    # Known commands while a menu is open: close menu cleanly, then fall through
    # so :more / :read / exit still work (fault-tolerant escape hatches).
    low = (user_input or "").strip().lower()
    if low in ("exit", "quit", "q", ":q") or low.startswith(":") or low.startswith(":read"):
        read_pick_state.clear()
        if mode == "directory" and low.startswith(":read"):
            read_state.clear()  # new :read replaces staged listing
        elif mode == "directory" and low == ":more":
            pass  # keep staged listing for paging
        print("  " + ui.dim("[directory menu closed]" if mode == "directory"
                            else "[read pick dismissed]"))
        return "fallthrough"

    # Ambiguous / normal chat — release the menu without trapping the user.
    read_pick_state.clear()
    if mode == "directory":
        print("  " + ui.dim("[directory menu closed — listing still staged]") + "\n")
    else:
        print("  " + ui.dim("[read pick dismissed]") + "\n")
    return "fallthrough"


def _read_ask_suffix(*, question: str | None = None, fname: str = "the attached file",
                     partial: bool = False) -> str:
    """Shared ask text for :read turns — citation grounding WITHOUT silencing reasoning.

    Soft failure we hit in the wild: an earlier "Answer only from the attached
    shown text" line made Aida refuse to hypothesize pathways/options that were
    not IN the document (energy-density ask). That conflated two different
    honesty duties:
      * inventing what the FILE says / unread pages  → forbidden
      * labeled analysis / hypotheses beyond the file → allowed (matches
        session._GUARD_TEXT IMAGINATION; presence, not a prison)
    """
    part = " (partial view; more of the file was not shown)" if partial else ""
    cite = (
        " For claims ABOUT what the attachment says, prefer short quotes or "
        "clear pointers into the shown text — do not invent unread spans or put "
        "words in the document's mouth. Never wrap your own paraphrase in quotes "
        "as if the document said it; only quote spans that appear verbatim above. "
        "If the user asks for analysis, options, pathways, or comparisons beyond "
        "what the text covers, you MAY reason and hypothesize using your knowledge; "
        "label clearly what came from the attachment vs. your own reasoning "
        "(e.g. 'The document doesn't propose methods; drawing on general knowledge…'). "
        "When a citation (author/year) appears only as a benchmark or reference in "
        "the text, do NOT attribute extra methods, conclusions, or research agendas "
        "to that source unless the shown text itself states them — put those ideas "
        "in your reasoning section as general knowledge, with uncertainty on specific "
        "multipliers/numbers. "
        "Lead with useful substance after a short BLUF — do not restate three times "
        "that the document lacks a plan. Tie pathways to the doc only when the "
        "connection is real; do not force-fit the user's biography into every option."
    )
    if question:
        return (f"The user attached {fname}{part} (shown above) and asks: {question}"
                f"{cite}")
    return (f"The user attached {fname}{part} (shown above). Briefly say what it is "
            "and what you can help with; then await their question. "
            "Do not invent unread file contents; you may later reason beyond the "
            "text when asked, as long as you label that clearly.")


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
    partial = not bool((read_state or {}).get("done", True))
    spec = (read_state or {}).get("search_spec")
    if spec is not None:
        import search_intent as si
        ask = si.format_search_ask(spec=spec, question=user_input or None)
    elif user_input:
        ask = _read_ask_suffix(question=user_input, fname=fname, partial=partial)
    else:
        ask = _read_ask_suffix(question=None, fname=fname, partial=partial)
    turn = "\n\n".join(staged) + "\n\n" + ask
    read_state["staged"] = []   # consumed
    return turn, True


def _current_attachment_matches(read_state: dict | None, path: str) -> bool:
    """Whether ``path`` is the file already held in the active read state.

    A file selected from a directory keeps ``browse_directory`` for sibling
    follow-ups. If the user's question names that same file, reuse its staged
    chunks instead of treating it as a fresh sibling read and resetting page 1.
    """
    if not read_state or read_state.get("kind") not in ("file", "csv"):
        return False
    source = read_state.get("source_path")
    if not source and read_state.get("browse_directory") and read_state.get("name"):
        source = os.path.join(read_state["browse_directory"], read_state["name"])
    if not source:
        return False
    import filereader
    return filereader.paths_same_target(str(source), path)


def _handle_more_command(session, read_state: dict) -> None:
    """Handle ':more' — reveal the next chunk of the currently-attached file/dir."""
    import filereader
    if not read_state or not read_state.get("text"):
        print("  " + ui.dim("[nothing to continue — attach a file with ':read <path>' first]") + "\n")
        return
    if read_state.get("done"):
        kind = read_state.get("kind") or "file"
        label = "listing" if kind == "directory" else "file"
        print("  " + ui.dim(
            f"[that was the whole of {read_state.get('name', 'the ' + label)} — "
            "nothing more to show]") + "\n")
        return
    kind = read_state.get("kind") or "file"
    next_chunk_no = int(read_state.get("chunk_no") or 1) + 1
    if kind == "directory":
        chunk = filereader.read_directory_chunk(
            read_state["text"], read_state["name"],
            char_offset=read_state["offset"], budget=read_state["budget"],
            chunk_no=next_chunk_no)
    else:
        chunk = filereader.read_chunk(
            read_state["text"], read_state["name"],
            char_offset=read_state["offset"], budget=read_state["budget"],
            chunk_no=next_chunk_no)
    read_state["offset"] = chunk["next_offset"]
    read_state["done"] = chunk["done"]
    read_state["chunk_no"] = chunk["chunk_no"]
    # STAGE the chunk (do NOT call the model): the user pages through the whole
    # attachment at their pace, then Aida answers once they ask (or press Enter).
    read_state.setdefault("staged", []).append(chunk["block"])
    end_word = "listing" if kind == "directory" else "file"
    tail = (" (':more' for more, or ask a question about it)" if not chunk["done"] else
            f" (end of {end_word} — ask a question about it, or press Enter for a quick orientation)")
    print("  " + ui.dim(f"[{read_state['name']} — chunk {chunk['chunk_no']}{tail}]"))


def cmd_chat(config: dict, fresh: bool = False) -> None:
    """Start an interactive chat session."""
    # Last :theme is in the merged config (config.local.yaml wins). Apply before
    # any chat output so a new session looks like the one they left. :theme
    # inside the loop still switches instantly and rewrites the local file.
    ui.set_theme(config.get("theme", ui.DEFAULT_THEME))
    ui.apply_canvas()
    try:
        _cmd_chat_body(config, fresh=fresh)
    finally:
        ui.release_canvas()


def _cmd_chat_body(config: dict, fresh: bool = False) -> None:
    """Interactive loop. Canvas apply/release is owned by cmd_chat()."""
    from mcm import MCM
    from critic import CriticInstance
    from session import ThreadSession

    llm = create_backend_from_config(config)
    set_default_backend(llm)

    mcm = MCM(
        adapter_version=config.get("adapter_version", 0),
        base_model=config.get("base_model", "llama3.2"),
        install_signal_handlers=True,
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
        chain_of_verification_enabled=config.get("chain_of_verification_enabled", True),
        cov_min_applied_d=config.get("cov_min_applied_d", 0.68),
        osmosis_enabled=config.get("osmosis_enabled", True),
        osmosis_boost=config.get("osmosis_boost", 0.01),
        osmosis_decay=config.get("osmosis_decay", 0.02),
        osmosis_boost_cap=config.get("osmosis_boost_cap", 0.15),
        osmosis_promotion_budget=config.get("osmosis_promotion_budget", 2),
        reflection_enabled=config.get("reflection_enabled", True),
        reflection_max_deliberations=config.get("reflection_max_deliberations", 1),
        reflection_on_session_end=config.get("reflection_on_session_end", False),
        document_osmosis_enabled=config.get("document_osmosis_enabled", True),
        background_gate_enabled=config.get("background_gate_enabled", True),
        background_max_deferral_s=config.get("background_max_deferral_s", 120.0),
        background_num_predict=config.get("background_num_predict", 512),
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
    _startup_terminal_check()
    try:
        import capabilities as _caps
        _nudges = _caps.nudge_lines(config)
        for _nudge in _nudges:
            print("  " + ui.dim(_nudge))
        if _nudges:
            print()
    except Exception:
        pass
    if fresh:
        print("[Fresh session — no prior context]\n")
    else:
        print("[Context restored]\n")

    for line in ui.wrap_hint_lines(
        "Type  :help  for commands  |  :status  for health  |  :learning  for how she learns",
    ):
        print(line)
    print(ui.dim("(Paste multiple lines = one turn. Commands are single-line only.)\n"))
    read_state: dict = {}   # paging state for the currently-attached file (:read/:more)
    read_pick_state: dict = {}  # interactive :read path disambiguation (y / 1-N)
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
        for line in ui.wrap_plain_lines(
            'Aida will SPEAK her short replies aloud. To silence her, just say '
            '"go silent" (or type \':voice off\'); say "speak again" to turn it back on.',
        ):
            print(line)
        print(ui.dim("  ':voice chatty' / ':voice terse' adjusts how much she speaks.\n"))
        if _tts_engine == "kokoro":
            voicelayer.prewarm_kokoro(_kokoro_model_path, _kokoro_voices_path)
    elif _env_voice == "1" and not _voice_available():
        print(ui.dim("  [voice: requested but no local speech engine available here "
                     "— staying text-only]\n"))

    try:
        while True:
            try:
                # Poka-yoke: while silenced, show resume hint ABOVE the prompt —
                # never embed ANSI in the readline prompt (breaks arrow/delete).
                _suffix = voicelayer.prompt_suffix(_voice_prefs)
                if _suffix:
                    print("  " + ui.dim(_suffix.strip()))
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
            if is_single_line:
                user_input = inputsafe.normalize_repl_input(user_input)

            if read_pick_state.get("candidates"):
                if not is_single_line:
                    read_pick_state.clear()
                    print("  " + ui.dim("[read pick cancelled — multi-line input]") + "\n")
                else:
                    pick_result = _try_read_pick_turn(
                        user_input, read_pick_state, session, config, read_state,
                        voice_prefs=_voice_prefs, voice_speak=_voice_speak,
                    )
                    if pick_result == "handled":
                        continue
                    if pick_result == "review_now":
                        # Empty Return on directory browse → orient on staged listing.
                        user_input = ""
                    # fallthrough: process the same line as a normal turn / command
                    # (review_now uses empty input + staged compose below)

            # Commands & quit are recognized ONLY on single-line input. A pasted
            # multi-line block is ALWAYS a chat turn — it can never quit the
            # session or switch models (defense in depth alongside read_multiline).
            if is_single_line:
                if user_input.lower() in ("exit", "quit", "q", ":q"):
                    break
                _cmd_kw = dict(
                    session=session, config=config,
                    voice_prefs=_voice_prefs, voice_speak=_voice_speak,
                    voice_available=_voice_available,
                    read_state=read_state, read_pick_state=read_pick_state,
                )
                if _dispatch_colon_command(user_input, **_cmd_kw):
                    session._pending_offer = None
                    continue
                if _try_pending_offer_turn(
                    user_input, session=session, config=config,
                    read_state=read_state, read_pick_state=read_pick_state,
                    voice_prefs=_voice_prefs, voice_speak=_voice_speak,
                ):
                    continue
                # Missed ':' — offer the reconstructed command; n sends to Aida.
                _offer = replcmds.missing_colon_offer(user_input)
                if _offer and _stdin_is_interactive():
                    print("  " + ui.dim(
                        f"[missing ':' — that looks like {_offer}]"))
                    if _ask_yes_no(
                        f"  Run as {_offer}? [Y/n]  (n sends to Aida) ",
                        default=True,
                    ):
                        if _dispatch_colon_command(_offer, **_cmd_kw):
                            session._pending_offer = None
                            continue
                    else:
                        print("  " + ui.dim("[sending to Aida as chat]") + "\n")
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
                # Follow-up after a user-attached directory: if the user names
                # exactly one direct-child file ("review index.html"), the runtime
                # re-reads and attaches its real bytes. This is deterministic,
                # non-recursive, symlink-safe, and does NOT grant the model
                # autonomous filesystem access.
                import filereader as _fr
                _browse_dir = (
                    read_state.get("source_path")
                    if read_state.get("kind") == "directory"
                    else read_state.get("browse_directory")
                )
                _child_path = _fr.resolve_directory_file_followup(
                    user_input, _browse_dir or "")
                # "Summarize Resume.html" after paging Resume.html is a question
                # about the current attachment, not authorization to reload it.
                # Let staged composition below submit all accumulated chunks.
                if _child_path and _current_attachment_matches(
                        read_state, _child_path):
                    _child_path = None
                if _child_path:
                    import shlex
                    print("  " + ui.dim(
                        f"[reading named file from attached directory: {_child_path}]"))
                    _cmd = f":read {shlex.quote(_child_path)} {user_input}"
                    _handle_read_command(
                        session, _cmd, config, read_state,
                        voice_prefs=_voice_prefs, voice_speak=_voice_speak,
                        read_pick_state=read_pick_state,
                    )
                    if read_state.get("text"):
                        read_state["browse_directory"] = _browse_dir
                    continue

                # Plain-language local read/list: route to the :read runtime when
                # the user names a local path (deterministic; never URLs/GitHub).
                # Conversational asks attach AND send this turn so Aida can read
                # the file inside the chat — staging-only is for bare :read.
                _nl_cmd = _nl_read_command(user_input)
                if _nl_cmd:
                    _path, _ = _parse_read_arg(_nl_cmd[len(":read"):].strip())
                    print("  " + ui.dim(f"[reading local path: {_path}]"))
                    _handle_read_command(session, _nl_cmd, config, read_state,
                                         voice_prefs=_voice_prefs, voice_speak=_voice_speak,
                                         read_pick_state=read_pick_state)
                    continue
            # Fold any file chunks the user staged with ':read'/':more' into this
            # turn (so paging no longer triggers an early reply). An empty line is
            # a valid "respond now" only when something is staged.
            if is_single_line and inputsafe.is_read_command_line(user_input):
                # Safety net: never send a bare :read line to the model (confabulation).
                _handle_read_command(session, user_input, config, read_state,
                                     voice_prefs=_voice_prefs, voice_speak=_voice_speak,
                                     read_pick_state=read_pick_state)
                continue
            # Poka-yoke: a mixed "just read :read ~/x" line that somehow skipped
            # the NL block above still must not reach the model without bytes.
            if is_single_line:
                _late_read = _nl_read_command(user_input)
                if _late_read:
                    _path, _ = _parse_read_arg(_late_read[len(":read"):].strip())
                    print("  " + ui.dim(f"[reading local path: {_path}]"))
                    _handle_read_command(session, _late_read, config, read_state,
                                         voice_prefs=_voice_prefs, voice_speak=_voice_speak,
                                         read_pick_state=read_pick_state)
                    continue
            turn_text, _submit = _compose_staged_turn(read_state, user_input)
            if not _submit:
                continue

            # Stream the reply token-by-token so it appears immediately. The
            # 'Model: ' prefix is printed once on the first real token; memory
            # corrections short-circuit before any token, so _streamed stays
            # False and we render them as a dim system line instead.
            _stream_state = {"started": False}
            _reply_writer: ui.ReplyStreamWriter | None = None

            # --- live "working" indicator while we wait for the FIRST token ---
            # The reply streams, so the only real wait is before the first token
            # arrives. A blinking indicator reassures the user the session is
            # alive and working; it ERASES ITSELF the instant streaming begins,
            # so it never overlaps the answer. Pure CLI display — no effect on
            # logic or the reply path. Skipped on non-TTY (piped) output.
            _spinner = _ThinkingIndicator(enabled=sys.stdout.isatty())
            _spinner.start()

            def _on_token(tok: str) -> None:
                nonlocal _reply_writer
                if _reply_writer is None:
                    _spinner.stop()                 # clear the indicator first
                    _reply_writer = ui.ReplyStreamWriter()
                    _stream_state["started"] = True
                _reply_writer.feed(tok)

            try:
                response = session.chat(turn_text, on_token=_on_token)
            finally:
                _spinner.stop()                     # also clears the [memory]/no-stream paths

            if response.startswith("[memory"):
                # No tokens were streamed; render the confirmation line.
                wrapped = "\n".join(ui.wrap_plain_lines(response))
                print("\n" + ui.dim(wrapped) + "\n")
            else:
                if _reply_writer is not None:
                    _reply_writer.finish()
                    print("\n")          # close the streamed line
                else:
                    from session import strip_emergent_markers_for_display
                    sys.stdout.write(ui.format_wrapped_reply(
                        replcmds.strip_offers(
                            strip_emergent_markers_for_display(response))))
                    print("\n")
                _dispatch_voice_after_reply(
                    response, session, _voice_prefs, read_state,
                    voice_speak=_voice_speak)
                _note_offer_from_reply(
                    session, response,
                    last_user=user_input,
                    has_attachment=bool(read_state.get("text")),
                )
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
        ui.print_session_end_summary(
            delta, end_summary=getattr(session, "_end_summary", {}) or {}
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
    from eval import format_tuning_gate_lines
    from tuning_facade import assess_gate_safe, coerce_tuning_params, score_deltas_safe, validate_mlx_model_path
    from tuner import build_training_data, trigger_tuning, estimate_training_stats
    from schemas import TuningJob
    import uuid

    params = coerce_tuning_params(config)
    deltas, scored, err = score_deltas_safe(config)
    if err:
        print("\n  ABORTED: " + err + "\n")
        return
    if not deltas:
        print("No thread deltas found. Run some sessions first.")
        return

    top_n = params["top_n_training"]
    version_in = params["adapter_version"]
    stats = estimate_training_stats(scored, top_n=top_n)
    gate, gate_err = assess_gate_safe(deltas, config, training_stats=stats)
    if gate_err or gate is None:
        print("\n  ABORTED: " + (gate_err or "Eval gate unavailable.") + "\n")
        return

    if not approve:
        _print_tune_preview(config)
        return

    for line in format_tuning_gate_lines(gate):
        print(line)

    if not gate.approved_for_run:
        print("\n  ABORTED: eval gate blocked approve. Resolve blockers and re-run preview.\n")
        return

    mlx_ok, mlx_detail = _mlx_lora_readiness()
    if not mlx_ok:
        print(f"\n  ABORTED: {mlx_detail}\n")
        return

    job_id = str(uuid.uuid4())[:8]
    try:
        build_training_data(scored, top_n=top_n, job_id=job_id)
    except (ValueError, OSError) as e:
        print(f"\n  ABORTED: could not build training data ({e})\n")
        return

    # Re-check gate after materializing training data (pole-yoke: same inputs, fresh count).
    stats2 = estimate_training_stats(scored, top_n=top_n)
    gate2, gate_err2 = assess_gate_safe(deltas, config, training_stats=stats2)
    if gate_err2 or gate2 is None or not gate2.approved_for_run:
        print("\n  ABORTED: eval gate failed after training-data build.\n")
        return

    version_out = version_in + 1
    composite = sum(st.weighted_score for st in scored[:top_n]) / min(top_n, len(scored))

    job = TuningJob(
        job_id=job_id,
        thread_ids_used=stats2["thread_ids"],
        adapter_version_in=version_in,
        adapter_version_out=version_out,
        approved=True,
        composite_signal=composite,
        status="approved",
    )

    model_path = params["mlx_model_path"]
    if not model_path:
        model_path = input("\nEnter path to MLX-converted model (not GGUF): ").strip()
    path_ok, path_detail = validate_mlx_model_path(model_path)
    if not path_ok:
        print(f"\n  ABORTED: {path_detail}\n")
        return

    try:
        trigger_tuning(job, model_path=path_detail, gate=gate2)
    except Exception as e:
        logger.exception("cmd_tune approve failed")
        print(f"\n  ABORTED: tuning run failed ({type(e).__name__}: {e})\n")


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
