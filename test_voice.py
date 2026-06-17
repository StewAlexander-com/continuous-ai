"""Tests for the operational voice (honest tone readout).

Covers: the math is a pure, monotonic, bounded readout of REAL signals; the
prompt line forbids narration; time phrasing is honest; and the whole thing is
side-effect-free (responsiveness — safe on the reply path).
"""
import sys
from datetime import datetime, timedelta, timezone

import voice


def _state(minutes=0.0, turns=0, work=0, hour=None):
    # Anchor the session start at the given hour, then add the minute offset so
    # session_minutes is honored. (Setting hour AFTER adding minutes would wipe
    # the delta — that was a test bug.)
    h = 14 if hour is None else hour
    start = datetime(2026, 6, 17, h, 0, tzinfo=timezone.utc)
    now = start + timedelta(minutes=minutes)
    return voice.compute_state(now=now, session_start=start,
                               substantive_turns=turns, work_units=work)


def test_membership_bounded():
    for m, t, w in [(0, 0, 0), (200, 99, 99), (10, 3, 4)]:
        s = _state(m, t, w)
        assert 0.0 <= s.freshness <= 1.0
        assert 0.0 <= s.engagement <= 1.0
    print("[PASS] membership degrees stay in [0,1]")


def test_freshness_decreases_with_time():
    early = _state(minutes=0).freshness
    mid = _state(minutes=20).freshness
    late = _state(minutes=60).freshness
    assert early > mid > late, f"freshness must decay: {early},{mid},{late}"
    assert early >= 0.99 and late <= 0.01
    print("[PASS] freshness decays smoothly as the session runs")


def test_engagement_increases_with_work_and_turns():
    light = _state(turns=0, work=0).engagement
    some = _state(turns=3, work=3).engagement
    deep = _state(turns=10, work=12).engagement
    assert light < some < deep, f"engagement must rise: {light},{some},{deep}"
    assert deep >= 0.99
    print("[PASS] engagement rises smoothly with real turns + work")


def test_descriptor_reflects_state():
    assert "light" in _state(turns=0, work=0).descriptor().lower()
    assert "deep" in _state(turns=10, work=12, minutes=60).descriptor().lower()
    print("[PASS] descriptor reflects the measured state")


def test_time_phrase_is_honest():
    morning = _state(hour=8).time_phrase().lower()
    evening = _state(hour=19).time_phrase().lower()
    assert "morning" in morning
    assert "evening" in evening
    print("[PASS] time phrase reflects the real hour of day")


def test_prompt_line_forbids_narration():
    line = voice.prompt_line(_state(turns=5, work=5))
    low = line.lower()
    # must instruct NOT to narrate / announce the state
    assert "do not" in low and ("narrate" in low or "announce" in low or "mention" in low)
    assert "tone" in low
    # substance-first guardrail present
    assert "first" in low or "true" in low
    print("[PASS] prompt line forbids narrating the state and keeps substance first")


def test_pure_function_no_side_effects():
    # Calling compute_state twice with the same inputs yields equal results and
    # touches nothing external (responsiveness: safe on the reply path).
    a = _state(minutes=10, turns=2, work=2)
    b = _state(minutes=10, turns=2, work=2)
    assert (a.freshness, a.engagement, a.descriptor()) == \
           (b.freshness, b.engagement, b.descriptor())
    print("[PASS] compute_state is pure/deterministic (safe & responsive)")


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
    print(f"\n{len(tests) - failed}/{len(tests)} voice checks passed")
    sys.exit(1 if failed else 0)
