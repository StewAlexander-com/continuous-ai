#!/usr/bin/env python3
"""Tests for learning_ui.py — tier copy single source of truth."""
import sys

from learning_ui import format_learning_commands_lines, format_learning_tiers_lines


def test_compact_tiers_mention_both_tiers():
    lines = format_learning_tiers_lines(expanded=False)
    text = "\n".join(lines)
    assert "Tier 1" in text and "Tier 2" in text
    assert ":learning" in text
    print("[PASS] compact tiers mention both tiers and :learning")


def test_expanded_tiers_cover_decision_guide():
    lines = format_learning_tiers_lines(expanded=True)
    text = "\n".join(lines)
    assert "Which tier?" in text
    assert "automatic" in text.lower()
    assert "opt-in" in text.lower()
    assert "eval gate" in text.lower() or "gate" in text.lower()
    assert "not loaded in chat" in text.lower()
    print("[PASS] expanded tiers include decision guide and honest limits")


def test_learning_commands_list_tune_and_learning():
    lines = format_learning_commands_lines()
    text = "\n".join(lines)
    assert ":learning" in text
    assert ":tune status" in text
    assert ":tune preview" in text
    print("[PASS] learning commands list includes :learning and tune cmds")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            failed += 1
            print(f"[FAIL] {t.__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} learning-ui checks passed")
    sys.exit(1 if failed else 0)
