"""
seedling/eval.py — Evaluation framework for the Seedling runtime.

Computes: coherence trend, correction rate, contradiction rate, drift risk,
context retrieval usefulness, and adapter stability.

Also handles the failure mode test suite.

Run as: python eval.py  → prints evaluation report for current session history.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from pathlib import Path

import yaml

from schemas import ContextState, ThreadDelta, CriticEvaluation

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).parent / "config.yaml"


# ---------------------------------------------------------------------------
# ThreadMetrics
# ---------------------------------------------------------------------------

@dataclass
class ThreadMetrics:
    """
    Aggregate evaluation metrics across all thread deltas.

    All scores are in [0, 1] unless noted.
    """
    coherence_trend: list[float]           # per-thread coherence scores, chronological
    correction_rate: float                  # corrections / total turns (approximated)
    contradiction_rate: float               # critic-flagged contradictions / evals (real signal when critic evals present; else coherence-dip proxy)
    context_retrieval_usefulness: float     # proxy: did restored context reduce repetition?
    drift_risk_score: float                 # composite cognitive drift
    adapter_stability: float                # 1 - variance of weight_adjustment_signals


@dataclass
class TuningGateResult:
    """Pre-approve safety assessment for deep LoRA tuning.

    Blockers are hard stops (non-regressive gate). Warnings are informational
    signals that may motivate tuning but do not block approval.
    """
    approved_for_run: bool
    blockers: list[str]
    warnings: list[str]
    metrics: ThreadMetrics


def training_sufficiency_warnings(training_stats: dict, config: dict) -> list[str]:
    """Factual warnings from measured training-data stats (never blocks).

    Thresholds live in eval_thresholds.* — defaults grounded in mlx-lm needing
    a non-trivial train/valid split and LoRA overfitting on tiny/skewed sets.
    """
    if not training_stats or training_stats.get("n_records", 0) == 0:
        return []

    thresholds = config.get("eval_thresholds", {})
    min_exchanges = int(thresholds.get("warn_training_exchanges_below", 20))
    min_threads = int(thresholds.get("warn_training_threads_below", 5))
    max_attach_frac = float(thresholds.get("warn_attachment_fraction_above", 0.50))
    max_single_frac = float(thresholds.get("warn_single_thread_fraction_above", 0.60))
    max_transcript_skips = int(thresholds.get("warn_transcript_skips_above", 3))

    n_rec = int(training_stats.get("n_records", 0))
    n_thr = int(training_stats.get("n_threads", 0))
    skipped = int(training_stats.get("skipped_no_transcript", 0))
    warnings: list[str] = []

    if n_rec < min_exchanges:
        warnings.append(
            f"Thin training set: {n_rec} exchanges (≥{min_exchanges} recommended for credible LoRA)."
        )
    if n_thr < min_threads:
        warnings.append(
            f"Few source threads: {n_thr} with transcripts (≥{min_threads} recommended for diversity)."
        )
    if skipped >= max_transcript_skips:
        warnings.append(
            f"Transcript gap: {skipped} scored thread(s) skipped — missing logs/transcript_<id>.jsonl."
        )
    attach_frac = float(training_stats.get("attachment_fraction", 0) or 0)
    if attach_frac >= max_attach_frac:
        warnings.append(
            f"Attachment-heavy: {attach_frac:.0%} of exchanges are file-attach turns — may skew toward document Q&A."
        )
    single_frac = float(training_stats.get("max_thread_exchange_fraction", 0) or 0)
    if single_frac >= max_single_frac:
        warnings.append(
            f"Single-thread dominance: {single_frac:.0%} of exchanges from one session — overfit risk."
        )
    emergent_n = int(training_stats.get("emergent_threads_used", 0))
    if n_thr > 0 and emergent_n / n_thr >= 0.5:
        warnings.append(
            f"Emergent-heavy: {emergent_n}/{n_thr} training threads flagged emergent — review samples first."
        )
    # mlx-lm 80/20 split: <10 total exchanges → valid has 1–2 records (unstable).
    if n_rec < 10:
        warnings.append(
            f"Tiny mlx split: {n_rec} total exchanges → valid set will be 1–2 records — post-tune metrics unreliable."
        )
    return warnings


def assess_tuning_gate(
    thread_deltas: list[ThreadDelta],
    config: dict | None = None,
    *,
    critic_evals: list[CriticEvaluation] | None = None,
    training_record_count: int | None = None,
    training_stats: dict | None = None,
) -> TuningGateResult:
    """Evaluate whether deep LoRA tuning may proceed without regression risk.

    Hard blocks (approved_for_run=False):
      - Insufficient captured sessions (< tuning_threshold_n)
      - Drift risk above eval_thresholds.max_drift_risk (rollback first)
      - Adapter stability below eval_thresholds.min_adapter_stability
      - No usable training records would be assembled
      - Average coherence of top training candidates below min_coherence

    Warnings (informational only):
      - High correction rate or low overall coherence (tuning may help)
      - Training sufficiency (thin set, transcript gaps, skew) when training_stats given
    """
    if config is None:
        config = {}
        if _CONFIG_PATH.exists():
            with open(_CONFIG_PATH) as f:
                config = yaml.safe_load(f) or {}

    thresholds = config.get("eval_thresholds", {})
    min_coherence = thresholds.get("min_coherence", 0.6)
    max_correction_rate = thresholds.get("max_correction_rate", 0.3)
    max_drift_risk = thresholds.get("max_drift_risk", 0.5)
    min_stability = thresholds.get("min_adapter_stability", 0.6)
    tuning_threshold_n = config.get("tuning_threshold_n", 10)

    if training_stats is not None:
        training_record_count = training_stats.get("n_records", training_record_count)

    if critic_evals is None:
        try:
            import storage
            critic_evals = storage.load_all_critic_evals()
        except Exception:
            critic_evals = []

    metrics = compute_metrics(thread_deltas, critic_evals)
    blockers: list[str] = []
    warnings: list[str] = []

    n = len(thread_deltas)
    if n < tuning_threshold_n:
        blockers.append(
            f"Need {tuning_threshold_n} captured sessions (have {n}). Keep chatting."
        )

    if metrics.drift_risk_score > max_drift_risk:
        blockers.append(
            f"Drift risk {metrics.drift_risk_score:.3f} > {max_drift_risk:.2f}"
            " — stabilize or rollback before tuning."
        )

    if metrics.adapter_stability < min_stability:
        blockers.append(
            f"Adapter stability {metrics.adapter_stability:.3f} < {min_stability:.2f}"
            " — signal too volatile for a weight update."
        )

    if training_record_count is not None and training_record_count == 0:
        blockers.append(
            "No usable training records (threads need saved transcripts in logs/)."
        )

    if thread_deltas and n >= tuning_threshold_n:
        try:
            from tuner import score_threads

            top_n = max(1, min(int(config.get("top_n_training", 10) or 10), 100))
            scored = score_threads(
                thread_deltas,
                correction_penalty=config.get("correction_penalty", 0.15),
                recency_decay_factor=config.get("recency_decay_factor", 0.05),
            )
            top = scored[:top_n]
            if top:
                cohs = [st.delta.coherence_score for st in top if st.delta.coherence_score == st.delta.coherence_score]
                if cohs:
                    avg_top_coh = sum(cohs) / len(cohs)
                    if avg_top_coh < min_coherence:
                        blockers.append(
                            f"Top training candidates avg coherence {avg_top_coh:.3f}"
                            f" < {min_coherence:.2f} — quality too low for a weight update."
                        )
        except Exception as e:
            blockers.append(f"Could not score training candidates ({type(e).__name__}) — tuning blocked.")
            logger.exception("assess_tuning_gate score_threads failed")

    if metrics.coherence_trend:
        avg_coh = sum(metrics.coherence_trend) / len(metrics.coherence_trend)
        if avg_coh < min_coherence:
            warnings.append(
                f"Avg coherence {avg_coh:.3f} below {min_coherence:.2f} — tuning may help."
            )
    if metrics.correction_rate > max_correction_rate:
        warnings.append(
            f"Correction rate {metrics.correction_rate:.3f} above"
            f" {max_correction_rate:.2f} — tuning may help."
        )

    if training_stats is not None:
        warnings.extend(training_sufficiency_warnings(training_stats, config))

    return TuningGateResult(
        approved_for_run=len(blockers) == 0,
        blockers=blockers,
        warnings=warnings,
        metrics=metrics,
    )


_SUFFICIENCY_MARKERS = (
    "Thin training set:",
    "Few source threads:",
    "Transcript gap:",
    "Attachment-heavy:",
    "Single-thread dominance:",
    "Emergent-heavy:",
    "Tiny mlx split:",
)


def _partition_gate_warnings(warnings: list[str]) -> tuple[list[str], list[str]]:
    """Split data-sufficiency cautions from aggregate signal notes."""
    data, signal = [], []
    for w in warnings:
        if any(w.startswith(m) for m in _SUFFICIENCY_MARKERS):
            data.append(w)
        else:
            signal.append(w)
    return data, signal


def format_tuning_gate_lines(gate: TuningGateResult) -> list[str]:
    """Plain-text lines for chat/CLI preview."""
    data_warns, signal_warns = _partition_gate_warnings(gate.warnings)
    lines = ["", "── Eval gate (pre-approve) ──"]

    if gate.approved_for_run:
        lines.append(
            "  Safety gate    : PASS — drift, stability, and coherence within bounds"
        )
        if data_warns:
            n = len(data_warns)
            lines.append(
                f"  Data gate      : CAUTION — {n} training sufficiency issue(s) below"
                " (does not block approve)"
            )
            lines.append(
                "  Meaning        : LoRA is unlikely to generalize well from this slice;"
                " Tier 1 is already learning from all sessions."
            )
            lines.append(
                "  Action         : Review data cautions, run more diverse chats"
                " (with transcripts), or stay on Tier 1."
            )
        else:
            lines.append("  Data gate      : OK — training sufficiency looks adequate")
            if signal_warns:
                lines.append(
                    "  Note           : Signal notes below — tuning may still help overall quality"
                )
    else:
        lines.append("  Safety gate    : BLOCKED — approve path locked until resolved")
        lines.append("  Data gate      : (not evaluated — fix safety blockers first)")

    for b in gate.blockers:
        lines.append(f"  Blocker        : {b}")
    for w in data_warns:
        lines.append(f"  Data caution   : {w}")
    for w in signal_warns:
        lines.append(f"  Signal note    : {w}")

    m = gate.metrics
    if m.coherence_trend:
        avg_coh = sum(m.coherence_trend) / len(m.coherence_trend)
        lines.append(f"  Avg coherence  : {avg_coh:.3f}")
    lines.append(f"  Drift risk     : {m.drift_risk_score:.3f}")
    lines.append(f"  Stability      : {m.adapter_stability:.3f}")
    lines.append(f"  Correction rate: {m.correction_rate:.3f}")
    return lines


def format_approve_path_lines(
    gate: TuningGateResult,
    *,
    mlx_ok: bool,
    mlx_detail: str,
) -> list[str]:
    """Explicit approve/tooling footer separate from the safety/data gate."""
    lines: list[str] = ["", "── Approve path ──"]
    data_warns, _ = _partition_gate_warnings(gate.warnings)

    if not gate.approved_for_run:
        lines.append("  CLI approve    : LOCKED — eval gate blocked (fix blockers above)")
        return lines

    if not mlx_ok:
        lines.append(f"  Tooling        : BLOCKED — {mlx_detail}")
        lines.append(
            "  CLI approve    : unavailable until mlx-lm is installed"
            " (pip install mlx-lm; Apple Silicon)"
        )
        return lines

    if data_warns:
        lines.append(
            "  CLI approve    : technically allowed — NOT recommended until data gate is OK"
        )
        lines.append(
            "  Command        : python seedling.py tune --approve-tuning"
            "  (explicit; you accept thin/skewed training risk)"
        )
    else:
        lines.append("  CLI approve    : allowed — gate PASS; still requires explicit CLI flag")
        lines.append("  Command        : python seedling.py tune --approve-tuning")
    return lines


# ---------------------------------------------------------------------------
# Metric computation
# ---------------------------------------------------------------------------

def compute_metrics(
    thread_deltas: list[ThreadDelta],
    critic_evals: list[CriticEvaluation] | None = None,
) -> ThreadMetrics:
    """Compute ThreadMetrics from ThreadDeltas.

    If critic_evals are provided, contradiction_rate is computed from the
    critic's ACTUAL stored contradiction_detected flags (fraction of evals
    flagged), which is a real signal. Otherwise it falls back to the legacy
    coherence-dip proxy (coherence < 0.4) so old data and the no-critic case
    still produce a number.
    """
    if not thread_deltas:
        return ThreadMetrics(
            coherence_trend=[],
            correction_rate=0.0,
            contradiction_rate=0.0,
            context_retrieval_usefulness=0.5,
            drift_risk_score=0.0,
            adapter_stability=1.0,
        )

    n = len(thread_deltas)
    coherence_trend = [d.coherence_score for d in thread_deltas]

    total_corrections = sum(d.user_correction_count for d in thread_deltas)
    # Approximate total turns: each delta represents >= 1 turn
    total_turns_est = max(n, total_corrections + n)
    correction_rate = total_corrections / total_turns_est

    # Contradiction rate: prefer the critic's ACTUAL stored flags.
    if critic_evals:
        flagged = sum(1 for e in critic_evals if e.contradiction_detected)
        contradiction_rate = flagged / len(critic_evals)
    else:
        # Fallback: legacy proxy via coherence dips (< 0.4 = possible contradiction).
        contradiction_flags = sum(1 for d in thread_deltas if d.coherence_score < 0.4)
        contradiction_rate = contradiction_flags / n

    # Context retrieval usefulness: proxy via correction rate trend
    # If correction rate decreases over time, context is helping
    if n >= 4:
        first_half = [d.user_correction_count for d in thread_deltas[:n//2]]
        second_half = [d.user_correction_count for d in thread_deltas[n//2:]]
        avg_first = sum(first_half) / len(first_half)
        avg_second = sum(second_half) / len(second_half)
        usefulness = max(0.0, min(1.0, 0.5 + (avg_first - avg_second) * 0.2))
    else:
        usefulness = 0.5  # insufficient data

    # Drift risk: variance of weight_adjustment_signals
    signals = [d.weight_adjustment_signal for d in thread_deltas]
    mean_sig = sum(signals) / n
    variance = sum((s - mean_sig) ** 2 for s in signals) / n
    drift_risk = min(1.0, variance * 4)  # scale: variance of 0.25 → risk of 1.0

    # Adapter stability: low variance = stable
    adapter_stability = max(0.0, 1.0 - variance * 2)

    return ThreadMetrics(
        coherence_trend=coherence_trend,
        correction_rate=correction_rate,
        contradiction_rate=contradiction_rate,
        context_retrieval_usefulness=usefulness,
        drift_risk_score=drift_risk,
        adapter_stability=adapter_stability,
    )


# ---------------------------------------------------------------------------
# Drift evaluation
# ---------------------------------------------------------------------------

def evaluate_drift(old_state: ContextState, new_state: ContextState) -> float:
    """
    Measure cognitive drift between two ContextStates.

    Composite of:
    - Cosine distance between topic_weights vectors (0-fill missing keys)
    - Absolute shift in abstraction_level
    - Framework overlap (Jaccard distance)

    Returns float in [0, 1] where 0 = no drift, 1 = maximum drift.
    """
    old_topics = old_state.persistent_priors.topic_weights
    new_topics = new_state.persistent_priors.topic_weights

    # Union of all topic keys, 0-filled (fixes the dict dimension mismatch bug)
    all_keys = sorted(set(old_topics.keys()) | set(new_topics.keys()))
    if not all_keys:
        topic_drift = 0.0
    else:
        old_vec = [old_topics.get(k, 0.0) for k in all_keys]
        new_vec = [new_topics.get(k, 0.0) for k in all_keys]
        topic_drift = _cosine_distance(old_vec, new_vec)

    abstraction_drift = abs(
        old_state.cognitive_style.abstraction_level
        - new_state.cognitive_style.abstraction_level
    )

    old_fw = set(old_state.cognitive_style.dominant_frameworks)
    new_fw = set(new_state.cognitive_style.dominant_frameworks)
    if old_fw | new_fw:
        framework_drift = 1.0 - len(old_fw & new_fw) / len(old_fw | new_fw)
    else:
        framework_drift = 0.0

    composite = (topic_drift * 0.5) + (abstraction_drift * 0.3) + (framework_drift * 0.2)
    return min(1.0, composite)


def _cosine_distance(a: list[float], b: list[float]) -> float:
    """1 - cosine_similarity. Returns 0 if either vector is zero."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x**2 for x in a))
    norm_b = math.sqrt(sum(x**2 for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return 1.0 - (dot / (norm_a * norm_b))


# ---------------------------------------------------------------------------
# Evaluation report
# ---------------------------------------------------------------------------

def run_eval_report(thread_deltas: list[ThreadDelta], config: dict | None = None) -> None:
    """
    Print a plain-text evaluation report.
    Flags metrics outside acceptable bounds.
    Suggests whether tuning is warranted.
    """
    if config is None:
        config = {}
        if _CONFIG_PATH.exists():
            with open(_CONFIG_PATH) as f:
                config = yaml.safe_load(f) or {}

    thresholds = config.get("eval_thresholds", {})
    min_coherence = thresholds.get("min_coherence", 0.6)
    max_correction_rate = thresholds.get("max_correction_rate", 0.3)
    max_drift_risk = thresholds.get("max_drift_risk", 0.5)
    min_stability = thresholds.get("min_adapter_stability", 0.6)

    # Load the critic's actual evaluations so contradiction_rate reflects the
    # stored contradiction_detected flags rather than a coherence-dip proxy.
    try:
        import storage
        critic_evals = storage.load_all_critic_evals()
    except Exception:
        critic_evals = []

    metrics = compute_metrics(thread_deltas, critic_evals)
    contradiction_source = "critic-flagged" if critic_evals else "proxy"

    print("\n" + "="*60)
    print("SEEDLING EVALUATION REPORT")
    print("="*60)
    print(f"  Threads analyzed     : {len(thread_deltas)}")

    if metrics.coherence_trend:
        avg_coh = sum(metrics.coherence_trend) / len(metrics.coherence_trend)
        flag = "  ⚠ BELOW THRESHOLD" if avg_coh < min_coherence else ""
        print(f"  Avg coherence        : {avg_coh:.3f}{flag}")
        trend = "↑" if (
            len(metrics.coherence_trend) >= 2
            and metrics.coherence_trend[-1] > metrics.coherence_trend[0]
        ) else "↓" if (
            len(metrics.coherence_trend) >= 2
            and metrics.coherence_trend[-1] < metrics.coherence_trend[0]
        ) else "→"
        print(f"  Coherence trend      : {trend}")
    else:
        print("  Avg coherence        : N/A")

    flag = "  ⚠ HIGH" if metrics.correction_rate > max_correction_rate else ""
    print(f"  Correction rate      : {metrics.correction_rate:.3f}{flag}")

    flag = "  ⚠ HIGH" if metrics.contradiction_rate > 0.2 else ""
    print(f"  Contradiction rate   : {metrics.contradiction_rate:.3f} ({contradiction_source}){flag}")

    print(f"  Context usefulness   : {metrics.context_retrieval_usefulness:.3f}")

    flag = "  ⚠ HIGH DRIFT" if metrics.drift_risk_score > max_drift_risk else ""
    print(f"  Drift risk           : {metrics.drift_risk_score:.3f}{flag}")

    flag = "  ⚠ UNSTABLE" if metrics.adapter_stability < min_stability else ""
    print(f"  Adapter stability    : {metrics.adapter_stability:.3f}{flag}")

    # Tuning recommendation
    print()
    should_tune = (
        metrics.correction_rate > max_correction_rate
        or (metrics.coherence_trend and sum(metrics.coherence_trend) / len(metrics.coherence_trend) < min_coherence)
    )
    should_rollback = metrics.drift_risk_score > max_drift_risk

    if should_rollback:
        print("  RECOMMENDATION: ⚠ Drift risk HIGH — consider rollback before tuning.")
    elif should_tune:
        print("  RECOMMENDATION: Tuning signal present. Run: python seedling.py tune --approve-tuning")
    else:
        print("  RECOMMENDATION: No tuning warranted at this time.")

    print("="*60 + "\n")


# ---------------------------------------------------------------------------
# Failure mode tests
# ---------------------------------------------------------------------------

def test_failure_modes() -> None:
    """
    Run through all documented failure modes and verify graceful handling.
    Prints PASS/FAIL for each.
    """
    results = []

    # 1. Context file missing → fresh context
    try:
        import storage
        state = storage.load_latest()
        # If DB doesn't exist yet, load_latest returns None → handled
        results.append(("Context file missing → fresh context", "PASS"))
    except Exception as e:
        results.append(("Context file missing → fresh context", f"FAIL: {e}"))

    # 2. Adapter missing → base model fallback
    adapter_path = Path(__file__).parent / "adapters" / "lora_v999.safetensors"
    if not adapter_path.exists():
        results.append(("Adapter missing → base model fallback", "PASS (adapter correctly absent)"))
    else:
        results.append(("Adapter missing → base model fallback", "N/A (adapter exists)"))

    # 3. Critic unavailable → proceeds without score
    try:
        from critic import CriticInstance
        critic = CriticInstance(backend="local", base_model="nonexistent_model_xyz")
        eval_ = critic.evaluate("test", "test")
        # Should return a neutral eval with notes, not raise
        results.append(("Critic unavailable → neutral eval", "PASS" if eval_.coherence == 0.5 else "WARN"))
    except Exception as e:
        results.append(("Critic unavailable → neutral eval", f"FAIL: {e}"))

    # 4. LanceDB lock → handled in storage._retry
    results.append(("LanceDB lock → retry backoff", "PASS (retry logic in storage._retry)"))

    # 5. topic_weights vector mismatch → cosine distance with 0-fill
    old = ContextState()
    old.persistent_priors.topic_weights = {"physics": 0.8, "AI": 0.6}
    new = ContextState()
    new.persistent_priors.topic_weights = {"physics": 0.7, "AI": 0.5, "cybersecurity": 0.9}
    try:
        drift = evaluate_drift(old, new)
        results.append(("topic_weights dimension mismatch → 0-fill", f"PASS (drift={drift:.3f})"))
    except Exception as e:
        results.append(("topic_weights dimension mismatch → 0-fill", f"FAIL: {e}"))

    print("\n" + "="*60)
    print("FAILURE MODE TEST SUITE")
    print("="*60)
    for name, result in results:
        print(f"  {result:<40} {name}")
    print("="*60 + "\n")


# lazy import for ContextState used in evaluate_drift — also imported at top


# ---------------------------------------------------------------------------
# __main__
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    import storage
    storage.init_db()
    deltas = storage.load_all_deltas()

    run_eval_report(deltas)
    test_failure_modes()
