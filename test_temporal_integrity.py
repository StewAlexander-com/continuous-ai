"""TEMPORAL INTEGRITY + visceral SYSTEM CLOCK (shared formatter)."""
import sys
from datetime import datetime, timedelta, timezone

import session as S
import voice


def test_guard_has_temporal_integrity():
    g = S._GUARD_TEXT.lower()
    assert "temporal integrity" in g
    assert "system clock" in g
    assert "inhabit that present" in g
    assert "has not yet occurred" in g
    assert "knowledge-cutoff monologue" in g or "knowledge-cutoff" in g
    assert "unrelated earned beliefs" in g
    assert "model id" in g
    print("[PASS] _GUARD_TEXT encodes TEMPORAL INTEGRITY (inhabit clock)")


def test_runtime_clock_line_delegates_to_voice():
    now = datetime(2026, 7, 15, 16, 45, tzinfo=timezone(timedelta(hours=-4)))
    line = S._runtime_clock_line(model_name="qwen3:30b-a3b", now=now)
    assert line == voice.system_clock_block(now, model_name="qwen3:30b-a3b")
    assert "2026" in line and "July" in line
    assert "qwen3:30b-a3b" in line
    assert "inhabit this present" in line.lower()
    print("[PASS] runtime clock line == visceral system_clock_block")


def test_voice_prompt_still_reinforces():
    now = datetime(2026, 7, 15, 16, 45, tzinfo=timezone(timedelta(hours=-4)))
    st = voice.compute_state(
        now=now, session_start=now, substantive_turns=0, work_units=0,
    )
    line = voice.prompt_line(st, model_name="qwen3:30b-a3b")
    assert "[SYSTEM CLOCK" in line
    assert "2026" in line
    assert "qwen3:30b-a3b" in line
    assert "time dimension" in line.lower()
    print("[PASS] per-turn voice line leads with SYSTEM CLOCK")


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
    print(f"\n{len(tests) - failed}/{len(tests)} temporal integrity checks passed")
    sys.exit(1 if failed else 0)
