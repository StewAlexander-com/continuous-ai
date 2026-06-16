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

_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE))


def _load_config() -> dict:
    config_path = _HERE / "config.yaml"
    if config_path.exists():
        with open(config_path) as f:
            return yaml.safe_load(f) or {}
    return {}


def _drain_pending_stdin() -> int:
    """
    Drain any lines already buffered on stdin (the signature of a multi-line
    paste, where the terminal delivers many lines at once).

    Returns the number of EXTRA lines drained (0 for normal single-line input).
    Uses select() for a non-blocking peek; on platforms/streams where that is
    unavailable (e.g. non-tty), returns 0 so behavior is unchanged.
    """
    import select
    drained = 0
    try:
        if not sys.stdin.isatty():
            return 0
        while select.select([sys.stdin], [], [], 0)[0]:
            line = sys.stdin.readline()
            if not line:
                break
            drained += 1
    except (OSError, ValueError):
        return drained
    return drained


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
                sys.stdout.write(f"\r\033[2m{self._label}{frames[i % len(frames)]}\033[0m")
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
                sys.stdout.write("\r\033[2K")
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


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

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
    )

    print("\n" + "="*60)
    print("  SEEDLING — Local AI Continuity Runtime")
    print("="*60)
    if not os.environ.get("PERPLEXITY_API_KEY") and config.get("critic_backend") == "perplexity":
        print("  ⚠  PERPLEXITY_API_KEY not set — critic will fall back to local")
        print("  Set it with: export PERPLEXITY_API_KEY=pplx-...")
    print()

    context_injection = session.start()
    if fresh:
        print("[Fresh session — no prior context]\n")
    else:
        print("[Context restored]\n")

    print("Type 'exit' or 'quit' to end the session.")
    print("(Single-line input only — multi-line pastes are rejected to avoid phantom turns.)\n")

    try:
        while True:
            try:
                user_input = input("You: ")
            except EOFError:
                break

            # Paste guard: if extra lines were buffered (a multi-line paste),
            # drain and reject the whole block. Reading line-by-line would
            # otherwise fire one model+critic call per pasted line.
            extra_lines = _drain_pending_stdin()
            if extra_lines:
                print(
                    f"\n[Rejected a {extra_lines + 1}-line paste] "
                    "Seedling takes one line per turn. "
                    "Send a single-line message, or end with 'exit'.\n"
                )
                continue

            user_input = user_input.strip()

            if user_input.lower() in ("exit", "quit", "q", ":q"):
                break
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
                    sys.stdout.write("\nModel: ")
                    _stream_state["started"] = True
                sys.stdout.write(tok)
                sys.stdout.flush()

            try:
                response = session.chat(user_input, on_token=_on_token)
            finally:
                _spinner.stop()                     # also clears the [memory]/no-stream paths

            if response.startswith("[memory"):
                # No tokens were streamed; render the confirmation line.
                print(f"\n\033[2m{response}\033[0m\n")
            else:
                if _stream_state["started"]:
                    print("\n")          # close the streamed line
                else:
                    print(f"\nModel: {response}\n")   # fallback if nothing streamed
                # Surface any live persona writes that happened this turn.
                for notice in getattr(session, "_memory_notices", []):
                    print(f"  \033[2m{notice}\033[0m")
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
                    print(f"  \033[2m\u231f {' \u00b7 '.join(bits)}\033[0m")

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
        print(f"\033[2m[model override: chat + critic = {model}]\033[0m")
    return cleaned


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

    elif command == "tune":
        approve = "--approve-tuning" in args
        cmd_tune(config, approve=approve)

    else:
        print(f"Unknown command: {command}")
        print("Run: python seedling.py --help")
        sys.exit(1)


if __name__ == "__main__":
    main()
