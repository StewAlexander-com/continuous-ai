#!/usr/bin/env python3
"""Tests for the read-only capabilities listing and one-time nudge.

Run: ./.venv/bin/python test_capabilities.py
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import capabilities as cap
import yaml


def _live_config() -> dict:
    return yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8")) or {}


def test_listing_matches_live_config():
    cfg = _live_config()
    text = cap.format_listing(cfg)
    assert "cannot turn anything on" in text
    assert "rga_search_enabled" in text
    assert "security_scan_enabled" in text
    rga_on = bool(cfg.get("rga_search_enabled"))
    scan_on = bool(cfg.get("security_scan_enabled"))
    if rga_on:
        assert "rga_search_enabled: ON" in text
    else:
        assert "rga_search_enabled: off" in text
    if scan_on:
        assert "security_scan_enabled: ON" in text
    else:
        assert "security_scan_enabled: off" in text
    if rga_on:
        assert list(cfg.get("rga_search_allowed_paths") or []), (
            "rga_search_enabled is on, so rga_search_allowed_paths must be non-empty"
        )
    print("[PASS] :capabilities listing matches live config.yaml")


def test_nudge_fires_once():
    cfg = {"rga_search_enabled": False, "security_scan_enabled": False}
    tmp = Path(tempfile.mkdtemp(prefix="seen_")) / "seen_features.json"
    first = cap.nudge_lines(cfg, seen_path=tmp)
    assert any("rga_search_enabled" in line for line in first)
    assert any("security_scan_enabled" in line for line in first)
    second = cap.nudge_lines(cfg, seen_path=tmp)
    assert second == []
    third = cap.nudge_lines(cfg, seen_path=tmp)
    assert third == []
    print("[PASS] nudge fires exactly once per new flag")


def test_command_and_nudge_cannot_mutate_flags():
    cfg = _live_config()
    before = dict(cfg)
    _ = cap.format_listing(cfg)
    tmp = Path(tempfile.mkdtemp(prefix="seen_")) / "seen.json"
    _ = cap.nudge_lines(cfg, seen_path=tmp)
    after = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8")) or {}
    assert after.get("rga_search_enabled") == before.get("rga_search_enabled")
    assert after.get("security_scan_enabled") == before.get("security_scan_enabled")
    print("[PASS] capabilities command and nudge cannot mutate flags")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
        except Exception as e:
            failed += 1
            print(f"[FAIL] {t.__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    raise SystemExit(1 if failed else 0)
