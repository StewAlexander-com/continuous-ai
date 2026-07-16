"""
tuning_facade.py — Pole-yoke facade for Tier 2 tuning UX.

Single entry point for :tune status / :tune preview / CLI preview / gate checks.
Handlers here must never raise into the chat REPL; faults surface as messages.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from inputsafe import normalize_repl_input

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).parent
_LOG_DIR = _ROOT / "logs"
_ADAPTER_DIR = _ROOT / "adapters"


def coerce_tuning_params(config: dict | None) -> dict:
    """Sanitize tuning knobs from config; never trust raw YAML values."""
    cfg = config or {}

    def _int(val, default: int, lo: int, hi: int) -> int:
        try:
            n = int(val)
        except (TypeError, ValueError):
            n = default
        return max(lo, min(hi, n))

    return {
        "tuning_threshold_n": _int(cfg.get("tuning_threshold_n"), 10, 1, 10_000),
        "top_n_training": _int(cfg.get("top_n_training"), 10, 1, 100),
        "adapter_version": _int(cfg.get("adapter_version"), 0, 0, 1_000_000),
        "correction_penalty": float(cfg.get("correction_penalty", 0.15) or 0.15),
        "recency_decay_factor": float(cfg.get("recency_decay_factor", 0.05) or 0.05),
        "eval_thresholds": cfg.get("eval_thresholds") if isinstance(cfg.get("eval_thresholds"), dict) else {},
        "mlx_model_path": str(cfg.get("mlx_model_path") or "").strip(),
    }


def parse_tune_subcommand(line: str) -> str | None:
    """Return 'status', 'preview', or None for unrecognized :tune variants."""
    s = normalize_repl_input(line).strip().lower()
    if s in (":tune", ":tune status"):
        return "status"
    if s == ":tune preview":
        return "preview"
    if s.startswith(":tune "):
        # e.g. ':tune  preview' after normalize won't match — collapse spaces
        rest = " ".join(s.split())
        if rest == ":tune preview":
            return "preview"
        if rest == ":tune status":
            return "status"
    return None


def looks_like_tune_command(first_line: str) -> bool:
    """True when a single-line input is any :tune subcommand."""
    return parse_tune_subcommand(first_line) is not None or (
        normalize_repl_input(first_line).strip().lower().startswith(":tune")
    )


def load_deltas_safe() -> tuple[list, str | None]:
    """Load thread deltas; on failure return ([], human-readable error)."""
    try:
        import storage
        storage.init_db()
        return storage.load_all_deltas(), None
    except Exception as e:
        logger.exception("load_deltas_safe failed")
        return [], f"Could not load session history ({type(e).__name__}: {e})"


def score_deltas_safe(config: dict) -> tuple[list, list, str | None]:
    """Return (deltas, scored, error). Never raises."""
    from tuner import score_threads

    params = coerce_tuning_params(config)
    deltas, err = load_deltas_safe()
    if err:
        return [], [], err
    if not deltas:
        return [], [], None
    try:
        scored = score_threads(
            deltas,
            correction_penalty=params["correction_penalty"],
            recency_decay_factor=params["recency_decay_factor"],
        )
        return deltas, scored, None
    except Exception as e:
        logger.exception("score_deltas_safe failed")
        return deltas, [], f"Could not score sessions ({type(e).__name__}: {e})"


def assess_gate_safe(
    deltas: list,
    config: dict,
    *,
    training_record_count: int | None = None,
    training_stats: dict | None = None,
) -> tuple[object | None, str | None]:
    """Run assess_tuning_gate; return (gate, error). Never raises."""
    from eval import assess_tuning_gate

    try:
        merged = {**(config or {}), **coerce_tuning_params(config)}
        gate = assess_tuning_gate(
            deltas,
            merged,
            training_record_count=training_record_count,
            training_stats=training_stats,
        )
        return gate, None
    except Exception as e:
        logger.exception("assess_gate_safe failed")
        return None, f"Eval gate failed ({type(e).__name__}: {e})"


def last_tuning_job_summary_safe() -> str | None:
    """Best-effort last job line from audit log; never raises."""
    log_file = _LOG_DIR / "tuning_jobs.jsonl"
    if not log_file.exists():
        return None
    try:
        last = None
        with open(log_file, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if line:
                    last = line
        if not last:
            return None
        job = json.loads(last)
        status = job.get("status", "?")
        v_in = job.get("adapter_version_in", "?")
        v_out = job.get("adapter_version_out", "?")
        return f"last job {status} (v{v_in} → v{v_out})"
    except Exception as e:
        logger.warning(f"last_tuning_job_summary_safe: {e}")
        return None


def adapter_artifact_status(adapter_version: int) -> str:
    """Human-readable LoRA artifact presence for a version."""
    if adapter_version <= 0:
        return "base model (no adapter)"
    dir_path = _ADAPTER_DIR / f"lora_v{adapter_version}"
    file_path = _ADAPTER_DIR / f"lora_v{adapter_version}.safetensors"
    if dir_path.is_dir():
        return f"{dir_path.name}/ present"
    if file_path.is_file():
        return f"{file_path.name} present"
    return f"v{adapter_version} recorded but artifact missing"


def session_learning_counts(session, config: dict) -> tuple[int, int, int, str | None]:
    """Return (thread_count, threshold, adapter_version, error). Never raises."""
    try:
        params = coerce_tuning_params(config)
        threshold = getattr(session, "tuning_threshold_n", params["tuning_threshold_n"])
        try:
            threshold = max(1, int(threshold))
        except (TypeError, ValueError):
            threshold = params["tuning_threshold_n"]

        mcm = getattr(session, "mcm", None)
        if mcm is None:
            return 0, threshold, params["adapter_version"], None

        state = mcm.current_state()
        thread_count = len(state.thread_deltas) if state else 0
        adapter_version = getattr(mcm, "adapter_version", params["adapter_version"])
        try:
            adapter_version = max(0, int(adapter_version))
        except (TypeError, ValueError):
            adapter_version = params["adapter_version"]
        return thread_count, threshold, adapter_version, None
    except Exception as e:
        logger.exception("session_learning_counts failed")
        return 0, 10, 0, f"Could not read learning state ({type(e).__name__}: {e})"


def validate_mlx_model_path(model_path: str) -> tuple[bool, str]:
    """Check MLX model path before approve; returns (ok, detail)."""
    p = (model_path or "").strip()
    if not p:
        return False, "No MLX model path — set mlx_model_path in config.yaml or enter at prompt."
    path = Path(p).expanduser()
    if not path.exists():
        return False, f"MLX model path not found: {path}"
    if not path.is_dir():
        return False, f"MLX model path must be a converted model directory: {path}"
    return True, str(path)


def session_end_learning_fields(session) -> dict:
    """Fields for session-end Memory line; empty dict on any fault."""
    try:
        threshold = getattr(session, "tuning_threshold_n", 10)
        try:
            threshold = max(1, int(threshold))
        except (TypeError, ValueError):
            threshold = 10
        state = session.mcm.current_state()
        thread_count = len(state.thread_deltas) if state else 0
        adapter_version = getattr(session.mcm, "adapter_version", 0)
        try:
            adapter_version = max(0, int(adapter_version))
        except (TypeError, ValueError):
            adapter_version = 0
        return {
            "thread_count": thread_count,
            "tuning_threshold_n": threshold,
            "adapter_version": adapter_version,
            "tuning_ready": thread_count >= threshold,
        }
    except Exception as e:
        logger.warning(f"session_end_learning_fields: {e}")
        return {}
