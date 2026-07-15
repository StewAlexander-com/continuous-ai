"""TEMPORAL AWARENESS (broader than wall-clock time awareness)."""
import sys
from datetime import datetime, timedelta, timezone

import session as S
import voice


def test_guard_temporal_vs_time_awareness():
    g = S._GUARD_TEXT.lower()
    assert "temporal awareness" in g
    assert "more than time awareness" in g
    assert "you have temporal awareness" in g
    # Stack named: clock, duration, sequence, continuity, finite window
    assert "duration" in g and "sequence" in g and "continuity" in g
    assert "finite shared window" in g or "finite" in g
    assert "no temporal awareness beyond the system clock" in g  # forbidden framing
    assert "only know the time" in g or "only a clock" in g or "mere" in g
    assert "has not yet occurred" in g
    assert "do not deny having temporal awareness" in g or "never say you have 'no temporal awareness'" in g
    print("[PASS] guard: temporal awareness ⊃ time awareness (honest stack)")


def test_runtime_clock_line_delegates_to_voice():
    now = datetime(2026, 7, 15, 16, 45, tzinfo=timezone(timedelta(hours=-4)))
    line = S._runtime_clock_line(model_name="qwen3:30b-a3b", now=now)
    assert line == voice.system_clock_block(now, model_name="qwen3:30b-a3b")
    assert "temporal awareness" in line.lower()
    assert "time awareness" in line.lower()
    assert "only a clock" in line.lower() or "shrink" in line.lower()
    print("[PASS] runtime clock distinguishes time vs temporal awareness")


def test_voice_prompt_names_full_stack():
    now = datetime(2026, 7, 15, 16, 45, tzinfo=timezone(timedelta(hours=-4)))
    st = voice.compute_state(
        now=now, session_start=now, substantive_turns=2, work_units=0,
    )
    # Force non-zero minutes for the duration line
    st.session_minutes = 12.0
    line = voice.prompt_line(st, model_name="qwen3:30b-a3b")
    assert "this session so far" in line.lower()
    assert "duration + sequence" in line.lower()
    assert "mere time awareness" in line.lower() or "shrink" in line.lower()
    assert "continuity" in line.lower()
    print("[PASS] per-turn prompt: duration + full temporal stack")


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
    print(f"\n{len(tests) - failed}/{len(tests)} temporal awareness checks passed")
    sys.exit(1 if failed else 0)
