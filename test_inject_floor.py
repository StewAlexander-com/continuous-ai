"""Tests for the context-injection safety floor (fixes the JSON-confab leak).

A confabulated insight (e.g. 'The user requires strictly valid JSON') was being
re-injected as the 'most recent insight from prior thread', so the model obeyed
it. The fix: latest_durable_insight() must skip insights that are emergent,
quarantined, OR low-coherence (<= MIN_INJECT_COHERENCE) — without deleting any
record. These tests pin that selection logic.
"""
import sys
from datetime import datetime, timezone, timedelta

from schemas import ThreadDelta, MIN_INJECT_COHERENCE
import schemas


def _delta(insight, coherence, *, emergent=False, quarantined=False, age_s=0):
    return ThreadDelta(
        timestamp=datetime.now(timezone.utc) - timedelta(seconds=age_s),
        insight_gained=insight,
        coherence_score=coherence,
        emergent=emergent,
        quarantined=quarantined,
    )


class _State:
    """Minimal stand-in exposing thread_deltas + the real method under test."""
    def __init__(self, deltas):
        self.thread_deltas = deltas
    # bind the real implementation
    latest_durable_insight = schemas.ContextState.latest_durable_insight


def test_low_coherence_insight_is_not_injected():
    # newest is a low-coherence confabulation; older is a good insight.
    deltas = [
        _delta("Good earned insight about dissent.", 0.85, age_s=100),
        _delta("The user requires strictly valid JSON.", 0.50, age_s=1),  # confab
    ]
    got = _State(deltas).latest_durable_insight()
    assert got is not None and "JSON" not in got.insight_gained, \
        f"low-coherence confab should be skipped, got: {got.insight_gained!r}"
    assert "dissent" in got.insight_gained
    print("[PASS] low-coherence (<=0.5) insight is skipped for injection")


def test_quarantined_insight_is_not_injected():
    deltas = [
        _delta("Good earned insight.", 0.85, age_s=100),
        _delta("The user requires strictly valid JSON.", 0.95, quarantined=True, age_s=1),
    ]
    got = _State(deltas).latest_durable_insight()
    assert got is not None and "JSON" not in got.insight_gained, \
        "a quarantined insight must never be injected, even at high coherence"
    print("[PASS] quarantined insight is excluded from injection (high coherence too)")


def test_emergent_still_skipped():
    deltas = [
        _delta("Good earned insight.", 0.85, age_s=100),
        _delta("A roleplay tangent.", 0.9, emergent=True, age_s=1),
    ]
    got = _State(deltas).latest_durable_insight()
    assert got is not None and "roleplay" not in got.insight_gained
    print("[PASS] emergent insight still skipped (original behavior preserved)")


def test_clean_recent_insight_is_injected():
    deltas = [
        _delta("Older insight.", 0.8, age_s=100),
        _delta("A clean, well-graded recent insight.", 0.9, age_s=1),
    ]
    got = _State(deltas).latest_durable_insight()
    assert got is not None and "clean, well-graded" in got.insight_gained, \
        "a clean recent insight must still be injected (no over-filtering)"
    print("[PASS] clean recent insight is still injected (no regression)")


def test_all_filtered_falls_back_not_to_quarantined():
    # every delta is low-coherence; fallback must still avoid quarantined/emergent.
    deltas = [
        _delta("Low but legit.", 0.4, age_s=100),
        _delta("Quarantined confab.", 0.95, quarantined=True, age_s=1),
    ]
    got = _State(deltas).latest_durable_insight()
    assert got is not None and got.quarantined is False, \
        "fallback must never surface a quarantined delta"
    assert "Low but legit" in got.insight_gained
    print("[PASS] when all are filtered, fallback still avoids quarantined/emergent")


def test_empty_history_returns_none():
    assert _State([]).latest_durable_insight() is None
    print("[PASS] empty history returns None (no crash)")


def test_backward_compat_default_quarantined_false():
    d = ThreadDelta(insight_gained="x", coherence_score=0.9)
    assert d.quarantined is False, "new field must default False for old records"
    print("[PASS] quarantined defaults False (backward-compatible)")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
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
    print(f"\n{len(tests) - failed}/{len(tests)} inject-floor checks passed")
    sys.exit(1 if failed else 0)
