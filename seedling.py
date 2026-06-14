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


def _setup_logging(level: str = "INFO") -> None:
    log_dir = _HERE / "logs"
    log_dir.mkdir(exist_ok=True)
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s  %(levelname)-8s  %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stderr),
            logging.FileHandler(log_dir / "seedling.log"),
        ],
    )


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

            response = session.chat(user_input)
            # A handled memory correction returns a '[memory...' confirmation
            # instead of a model reply — render it as a dim system line, not
            # as 'Model:', and skip the duplicate per-turn notices.
            if response.startswith("[memory"):
                print(f"\n\033[2m{response}\033[0m\n")
            else:
                print(f"\nModel: {response}\n")
                # Surface any live persona writes that happened this turn.
                for notice in getattr(session, "_memory_notices", []):
                    print(f"  \033[2m{notice}\033[0m")

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
