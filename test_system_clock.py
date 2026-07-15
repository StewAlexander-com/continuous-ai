"""Visceral host system clock — portable across macOS / Linux / Windows.

Single source of truth: voice.system_clock_block. Session-start and per-turn
injects both call it. Stdlib only; no NTP, shell, or model call.
"""
import sys
from datetime import datetime, timedelta, timezone

import session as S
import voice


def _fixed(hours: int) -> datetime:
    """Aware stamp with a fixed offset (Windows-like / no IANA key)."""
    tz = timezone(timedelta(hours=hours), name="TEST")
    return datetime(2026, 7, 15, 16, 55, 30, tzinfo=tz)


def test_aware_local_attaches_tz_to_naive():
    naive = datetime(2026, 7, 15, 16, 55)
    aware = voice.aware_local(naive)
    assert aware.tzinfo is not None
    assert aware.year == 2026 and aware.month == 7 and aware.day == 15
    print("[PASS] naive wall time becomes local-aware")


def test_utc_offset_label_portable():
    assert voice.utc_offset_label(_fixed(-4)) == "UTC-04:00"
    assert voice.utc_offset_label(_fixed(5)) == "UTC+05:00"
    assert voice.utc_offset_label(_fixed(0)) == "UTC+00:00"
    print("[PASS] UTC offset label is portable (no strftime %-flags)")


def test_system_clock_block_visceral_and_silent():
    block = voice.system_clock_block(
        _fixed(-4), model_name="qwen3:30b-a3b",
        session_minutes=8.0, substantive_turns=3,
    )
    low = block.lower()
    assert "[SYSTEM CLOCK" in block
    assert "do not recite" in low or "silent" in low
    assert "Wednesday" in block and "July" in block and "2026" in block
    assert "UTC-04:00" in block
    assert "qwen3:30b-a3b" in block
    assert "this session so far" in low
    assert "time awareness" in low and "temporal awareness" in low
    assert "only a clock" in low
    print("[PASS] system_clock_block: time + duration; temporal ⊃ time")


def test_session_start_uses_same_formatter():
    a = voice.system_clock_block(_fixed(-4), model_name="m:1")
    b = S._runtime_clock_line(model_name="m:1", now=_fixed(-4))
    assert a == b
    print("[PASS] session-start clock == voice.system_clock_block (one source)")


def test_prompt_line_leads_with_system_clock_silent():
    st = voice.compute_state(
        now=_fixed(-4),
        session_start=_fixed(-4),
        substantive_turns=0,
        work_units=0,
    )
    line = voice.prompt_line(st, model_name="qwen3:30b-a3b")
    assert line.strip().startswith("[SYSTEM CLOCK")
    assert "do not mention the date or time" in line.lower()
    assert "mere time awareness" in line.lower() or "temporal awareness" in line.lower()
    assert "knowledge-cutoff monologue" in line.lower()
    print("[PASS] per-turn prompt leads with SYSTEM CLOCK (temporal ⊃ time)")


def test_guard_silent_orientation():
    g = S._GUARD_TEXT.lower()
    assert "system clock" in g
    assert "temporal awareness" in g
    assert "more than time awareness" in g
    assert "duration" in g and "sequence" in g
    print("[PASS] TEMPORAL AWARENESS ⊃ time awareness; silent unless relevant")


def test_clock_phrase_matches_legacy_shape():
    phrase = voice.clock_phrase(_fixed(-4)).lower()
    assert "2026" in phrase and "jul" in phrase
    assert "afternoon" in phrase
    print("[PASS] clock_phrase keeps short human stamp for status_line")


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
    print(f"\n{len(tests) - failed}/{len(tests)} system-clock checks passed")
    sys.exit(1 if failed else 0)
