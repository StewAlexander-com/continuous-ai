"""Context-aware voice block: light vs standard turn weight."""
import sys
from datetime import datetime, timedelta, timezone

import voice


def _fixed():
    return datetime(2026, 7, 15, 17, 30, tzinfo=timezone(timedelta(hours=-4)))


def test_classify_light_turns():
    for t in (
        "Hi", "Hi Aida", "hello", "Hey!", "Thanks", "Thank you",
        "ok", "good morning", "bye",
    ):
        assert voice.classify_turn_weight(t) == "light", t
    print("[PASS] greetings/acks classify as light")


def test_classify_standard_turns():
    for t in (
        "Do you have temporal awareness?",
        "Name three LLMs released after February 2025",
        "How does deliberation work?",
        "Hi — can you review this PR and list risks?",
    ):
        assert voice.classify_turn_weight(t) == "standard", t
    print("[PASS] substantive asks classify as standard")


def test_light_prompt_is_leaner_than_standard():
    st = voice.compute_state(
        now=_fixed(), session_start=_fixed(),
        substantive_turns=2, work_units=1,
    )
    light = voice.prompt_line(st, model_name="qwen3:30b-a3b", turn_weight="light")
    std = voice.prompt_line(st, model_name="qwen3:30b-a3b", turn_weight="standard")
    assert len(light) < len(std)
    assert "[TURN: light" in light
    assert "do not inventory topics" in light.lower()
    assert "this session so far" not in light.lower()
    assert "this session so far" in std.lower()
    assert "2026" in light and "2026" in std
    print("[PASS] light prompt leaner; still has calendar year")


def test_both_forbid_meta_narration():
    st = voice.compute_state(
        now=_fixed(), session_start=_fixed(),
        substantive_turns=0, work_units=0,
    )
    for w in ("light", "standard"):
        line = voice.prompt_line(st, turn_weight=w).lower()
        assert "parenthetical process notes" in line or "bluf scores" in line
    print("[PASS] light+standard forbid BLUF/disposition meta footnotes")


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
    print(f"\n{len(tests) - failed}/{len(tests)} context-aware voice checks passed")
    sys.exit(1 if failed else 0)
