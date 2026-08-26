#!/usr/bin/env python3
"""Tests for flags.py — the only write path for boolean capability flags.

Run: ./.venv/bin/python test_flags.py
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import flags

SAMPLE = """\
# --- Search / scan -------------------------------------------------
# Comments in this file explain trade-offs and must survive an edit.
rga_search_enabled: true
rga_search_allowed_paths:
  - /Users/someone/project
rga_search_max_hits: 50
security_scan_enabled: false        # read-only; no git history, no auto-fix

# --- Integrity guards (NOT chat-settable) --------------------------
caution_controller_enabled: true
deliberation_enabled: true

nested:
  security_scan_enabled: false
"""


def _tmp_cfg() -> Path:
    p = Path(tempfile.mkdtemp(prefix="flags_")) / "config.yaml"
    p.write_text(SAMPLE, encoding="utf-8")
    return p


def test_flip_preserves_comments_and_inline_comment():
    p = _tmp_cfg()
    ok, msg = flags.set_flag_yaml(p, "security_scan_enabled", True)
    assert ok, msg
    text = p.read_text(encoding="utf-8")
    assert "security_scan_enabled: true        # read-only; no git history, no auto-fix" in text, text
    assert "# Comments in this file explain trade-offs and must survive an edit." in text
    assert "rga_search_max_hits: 50" in text
    assert "- /Users/someone/project" in text
    # line count unchanged: a flip must never add or drop lines
    assert len(text.splitlines()) == len(SAMPLE.splitlines())
    print("[PASS] flip preserves comments, inline comment, and line count")


def test_nested_same_named_key_is_untouched():
    p = _tmp_cfg()
    ok, _ = flags.set_flag_yaml(p, "security_scan_enabled", True)
    assert ok
    text = p.read_text(encoding="utf-8")
    assert "  security_scan_enabled: false" in text, "indented key must not be rewritten"
    print("[PASS] nested same-named key untouched")


def test_integrity_guards_are_refused():
    p = _tmp_cfg()
    for guarded in ("caution_controller_enabled", "deliberation_enabled",
                    "chain_of_verification_enabled", "collaborative_wall_enabled"):
        assert not flags.is_toggleable(guarded), guarded
        ok, msg = flags.set_flag_yaml(p, guarded, False)
        assert not ok, f"{guarded} must not be chat-settable"
        assert "integrity guard" in msg, msg
    # and the file is byte-identical after all those refusals
    assert p.read_text(encoding="utf-8") == SAMPLE
    print("[PASS] integrity guards refused, file untouched")


def test_already_at_value_is_not_a_write():
    p = _tmp_cfg()
    before = p.read_text(encoding="utf-8")
    ok, msg = flags.set_flag_yaml(p, "rga_search_enabled", True)
    assert not ok and "already" in msg, msg
    assert p.read_text(encoding="utf-8") == before
    print("[PASS] no-op flip does not rewrite the file")


def test_round_trip_off_and_on():
    p = _tmp_cfg()
    assert flags.read_flag_yaml(p, "rga_search_enabled") is True
    ok, _ = flags.set_flag_yaml(p, "rga_search_enabled", False)
    assert ok
    assert flags.read_flag_yaml(p, "rga_search_enabled") is False
    ok, _ = flags.set_flag_yaml(p, "rga_search_enabled", True)
    assert ok
    assert flags.read_flag_yaml(p, "rga_search_enabled") is True
    print("[PASS] off/on round trip")


def test_missing_key_and_missing_file():
    p = _tmp_cfg()
    ok, msg = flags.set_flag_yaml(p, "rga_search_enabled", False)
    assert ok
    ghost = p.parent / "nope.yaml"
    ok, msg = flags.set_flag_yaml(ghost, "rga_search_enabled", True)
    assert not ok and "not found" in msg, msg
    print("[PASS] missing file reported, not raised")


def test_normalize_accepts_short_names_but_never_guesses_across_flags():
    assert flags.normalize_flag_name("scan") == "security_scan_enabled"
    assert flags.normalize_flag_name("search") == "rga_search_enabled"
    assert flags.normalize_flag_name("security_scan_enabled") == "security_scan_enabled"
    assert flags.normalize_flag_name(":scan") == "security_scan_enabled"
    # An unknown token must come back unresolved so the caller refuses it,
    # rather than being mapped onto whichever flag looks closest.
    for junk in ("caution", "honesty", "guards", "everything", ""):
        assert not flags.is_toggleable(flags.normalize_flag_name(junk)), junk
    print("[PASS] short names resolve; unknown tokens are not guessed")


def test_apply_to_config_is_live():
    cfg = {"security_scan_enabled": False}
    flags.apply_flag_to_config(cfg, "security_scan_enabled", True)
    assert cfg["security_scan_enabled"] is True
    print("[PASS] in-memory apply takes effect immediately")


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
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    raise SystemExit(1 if failed else 0)
