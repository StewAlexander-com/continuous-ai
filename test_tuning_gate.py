#!/usr/bin/env python3
"""Tests for RDST eval gate + tune preview helpers (no live MLX/server)."""
import sys
import uuid

from schemas import ThreadDelta
from eval import assess_tuning_gate, format_tuning_gate_lines, format_approve_path_lines, training_sufficiency_warnings
from tuner import estimate_training_stats, format_scoring_table, score_threads


def _delta(coherence=0.8, corrections=0, signal=0.0) -> ThreadDelta:
    return ThreadDelta(
        thread_id=str(uuid.uuid4()),
        coherence_score=coherence,
        user_correction_count=corrections,
        weight_adjustment_signal=signal,
    )


def test_gate_blocks_insufficient_sessions():
    cfg = {"tuning_threshold_n": 10, "eval_thresholds": {}}
    gate = assess_tuning_gate([_delta()], cfg, training_record_count=5)
    assert not gate.approved_for_run
    assert any("10 captured sessions" in b or "Need 10" in b for b in gate.blockers)
    print("[PASS] gate blocks when below session threshold")


def test_gate_blocks_high_drift():
    cfg = {
        "tuning_threshold_n": 2,
        "eval_thresholds": {"max_drift_risk": 0.1, "min_adapter_stability": 0.0, "min_coherence": 0.1},
    }
    deltas = [_delta(signal=-1.0), _delta(signal=1.0)]
    gate = assess_tuning_gate(deltas, cfg, training_record_count=10)
    assert not gate.approved_for_run
    assert any("Drift risk" in b for b in gate.blockers)
    print("[PASS] gate blocks high drift risk")


def test_gate_blocks_low_training_coherence():
    cfg = {
        "tuning_threshold_n": 2,
        "top_n_training": 2,
        "eval_thresholds": {"min_coherence": 0.7, "max_drift_risk": 1.0, "min_adapter_stability": 0.0},
    }
    deltas = [_delta(coherence=0.3), _delta(coherence=0.35)]
    gate = assess_tuning_gate(deltas, cfg, training_record_count=4)
    assert not gate.approved_for_run
    assert any("Top training candidates" in b for b in gate.blockers)
    print("[PASS] gate blocks low-quality training candidates")


def test_gate_passes_healthy_history():
    cfg = {
        "tuning_threshold_n": 2,
        "top_n_training": 2,
        "eval_thresholds": {
            "min_coherence": 0.5,
            "max_drift_risk": 0.9,
            "min_adapter_stability": 0.0,
            "max_correction_rate": 0.5,
        },
    }
    deltas = [_delta(coherence=0.85, signal=0.1), _delta(coherence=0.82, signal=0.12)]
    gate = assess_tuning_gate(deltas, cfg, training_record_count=6)
    assert gate.approved_for_run, gate.blockers
    assert not gate.blockers
    print("[PASS] gate passes healthy history")


def test_gate_blocks_zero_training_records():
    cfg = {"tuning_threshold_n": 1, "eval_thresholds": {"max_drift_risk": 1.0, "min_adapter_stability": 0.0}}
    gate = assess_tuning_gate([_delta()], cfg, training_record_count=0)
    assert not gate.approved_for_run
    assert any("No usable training records" in b for b in gate.blockers)
    print("[PASS] gate blocks when no training records")


def test_format_gate_lines_includes_pass_or_blocked():
    cfg = {"tuning_threshold_n": 1, "eval_thresholds": {"max_drift_risk": 1.0, "min_adapter_stability": 0.0}}
    gate = assess_tuning_gate([_delta()], cfg, training_record_count=3)
    text = "\n".join(format_tuning_gate_lines(gate))
    assert "Eval gate" in text
    assert "PASS" in text or "BLOCKED" in text
    assert "Safety gate" in text
    print("[PASS] format_tuning_gate_lines renders gate status")


def test_estimate_training_stats_empty_without_transcripts():
    scored = score_threads([_delta(), _delta()])
    stats = estimate_training_stats(scored, top_n=2)
    assert stats["n_records"] == 0
    assert stats["n_threads"] == 0
    print("[PASS] estimate_training_stats dry-run without transcripts")


def test_format_scoring_table_non_empty():
    scored = score_threads([_delta(), _delta()])
    lines = format_scoring_table(scored)
    assert any("RDST Scoring" in ln for ln in lines)
    assert len(lines) >= 4
    print("[PASS] format_scoring_table renders rows")


def test_sufficiency_warnings_user_like_slice():
    """Reproduces a PASS gate with credible thin/skew warnings (12 ex / 4 threads)."""
    stats = {
        "n_records": 12,
        "n_threads": 4,
        "skipped_no_transcript": 6,
        "attachment_fraction": 0.75,
        "max_thread_exchange_fraction": 0.33,
        "emergent_threads_used": 0,
        "top_n": 10,
    }
    cfg = {"eval_thresholds": {}}
    warns = training_sufficiency_warnings(stats, cfg)
    assert any("Thin training set" in w for w in warns)
    assert any("Few source threads" in w for w in warns)
    assert any("Transcript gap" in w for w in warns)
    assert any("Attachment-heavy" in w for w in warns)
    assert not any("Tiny mlx split" in w for w in warns)  # 12 ex → valid ~2–3, warn only below 10
    print("[PASS] sufficiency warnings for thin attachment-heavy slice")


def test_gate_pass_includes_sufficiency_warnings():
    cfg = {
        "tuning_threshold_n": 2,
        "top_n_training": 10,
        "eval_thresholds": {"max_drift_risk": 1.0, "min_adapter_stability": 0.0, "min_coherence": 0.1},
    }
    deltas = [_delta(coherence=0.85), _delta(coherence=0.82)]
    stats = {
        "n_records": 8,
        "n_threads": 2,
        "skipped_no_transcript": 0,
        "attachment_fraction": 0.0,
        "max_thread_exchange_fraction": 0.5,
        "emergent_threads_used": 0,
    }
    gate = assess_tuning_gate(deltas, cfg, training_stats=stats)
    assert gate.approved_for_run
    assert any("Thin training set" in w for w in gate.warnings)
    text = "\n".join(format_tuning_gate_lines(gate))
    assert "Safety gate" in text and "Data gate" in text and "CAUTION" in text
    footer = "\n".join(format_approve_path_lines(gate, mlx_ok=False, mlx_detail="mlx-lm not installed"))
    assert "Tooling" in footer and "BLOCKED" in footer
    print("[PASS] gate PASS surfaces explicit safety/data gates")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            failed += 1
            print(f"[FAIL] {t.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"[ERROR] {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} tuning-gate checks passed")
    sys.exit(1 if failed else 0)
