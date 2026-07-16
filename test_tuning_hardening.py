#!/usr/bin/env python3
"""Hardening tests for tuning_facade + fault-tolerant tuning UX."""
import sys
import uuid
from unittest import mock

from schemas import ThreadDelta
from tuning_facade import (
    adapter_artifact_status,
    coerce_tuning_params,
    parse_tune_subcommand,
    session_end_learning_fields,
    validate_mlx_model_path,
)
from eval import assess_tuning_gate


def _delta(coherence=0.8, signal=0.1) -> ThreadDelta:
    return ThreadDelta(
        thread_id=str(uuid.uuid4()),
        coherence_score=coherence,
        weight_adjustment_signal=signal,
    )


def test_coerce_tuning_params_clamps_bad_config():
    p = coerce_tuning_params({
        "tuning_threshold_n": -5,
        "top_n_training": "not-a-number",
        "adapter_version": -1,
    })
    assert p["tuning_threshold_n"] == 1
    assert p["top_n_training"] == 10
    assert p["adapter_version"] == 0
    print("[PASS] coerce_tuning_params clamps invalid YAML")


def test_parse_tune_subcommand_variants():
    assert parse_tune_subcommand(":tune") == "status"
    assert parse_tune_subcommand(":tune status") == "status"
    assert parse_tune_subcommand(":tune preview") == "preview"
    assert parse_tune_subcommand(":tune  preview") == "preview"
    assert parse_tune_subcommand("You: :tune preview") == "preview"
    assert parse_tune_subcommand(":tune go") is None
    print("[PASS] parse_tune_subcommand handles variants")


def test_validate_mlx_model_path():
    ok, msg = validate_mlx_model_path("")
    assert not ok
    ok, msg = validate_mlx_model_path("/no/such/path-xyz")
    assert not ok and "not found" in msg
    print("[PASS] validate_mlx_model_path rejects empty and missing")


def test_gate_blocks_on_score_failure():
    cfg = {"tuning_threshold_n": 1, "eval_thresholds": {"max_drift_risk": 1.0, "min_adapter_stability": 0.0, "min_coherence": 0.1}}
    deltas = [_delta()]
    with mock.patch("tuner.score_threads", side_effect=RuntimeError("boom")):
        gate = assess_tuning_gate(deltas, cfg, training_record_count=5)
    assert not gate.approved_for_run
    assert gate.blockers
    print("[PASS] gate blocks when score_threads fails")


def test_session_end_learning_fields_tolerates_broken_mcm():
    class _Broken:
        mcm = None
        tuning_threshold_n = 10

    fields = session_end_learning_fields(_Broken())
    assert fields == {}
    print("[PASS] session_end_learning_fields empty on broken session")


def test_session_end_learning_fields_ok():
    class _State:
        thread_deltas = [object(), object()]

    class _MCM:
        adapter_version = 2
        def current_state(self):
            return _State()

    class _Sess:
        mcm = _MCM()
        tuning_threshold_n = 10

    fields = session_end_learning_fields(_Sess())
    assert fields["thread_count"] == 2
    assert fields["tuning_ready"] is False
    print("[PASS] session_end_learning_fields on healthy session")


def test_adapter_artifact_status_variants():
    assert "base model" in adapter_artifact_status(0)
    assert "missing" in adapter_artifact_status(99999)
    print("[PASS] adapter_artifact_status handles missing artifacts")


def test_dispatch_unknown_tune_command(capsys=None):
    import io
    import contextlib
    import seedling

    class _Sess:
        pass

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        seedling._dispatch_tune_command(_Sess(), {}, ":tune go")
    out = buf.getvalue()
    assert "Unknown :tune command" in out
    print("[PASS] dispatch unknown :tune subcommand is graceful")


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
    print(f"\n{len(tests) - failed}/{len(tests)} tuning-hardening checks passed")
    sys.exit(1 if failed else 0)
