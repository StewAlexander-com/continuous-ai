#!/usr/bin/env python3
"""Phase validation harness for the rga-search / capabilities expansion.

Runs new tests, then a regression slice with flags at their default (off).
Does not vendor rga; live rga cases skip if the binary is missing.

Run: ./.venv/bin/python test_rga_capability_harness.py
"""
from __future__ import annotations

import compileall
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent


def _run(script: str) -> int:
    print(f"\n--- {script} ---")
    proc = subprocess.run([sys.executable, str(ROOT / script)], cwd=str(ROOT))
    return proc.returncode


def test_config_gates_are_consistent():
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8")) or {}
    assert "rga_search_enabled" in cfg
    assert "security_scan_enabled" in cfg
    if cfg.get("rga_search_enabled"):
        assert list(cfg.get("rga_search_allowed_paths") or []), (
            "enabled search requires a non-empty allowlist"
        )
    print("[PASS] search/scan flags present; allowlist required when search is on")


def test_readers_untouched():
    """Additive-only: the named readers must still exist and be importable."""
    import docxreader  # noqa: F401
    import filereader  # noqa: F401
    import pdfreader  # noqa: F401
    print("[PASS] filereader/docxreader/pdfreader still import")


def main() -> int:
    test_config_gates_are_consistent()
    test_readers_untouched()
    if not compileall.compile_dir(str(ROOT), quiet=1, maxlevels=0):
        print("[FAIL] compileall")
        return 1
    print("[PASS] compileall *.py")

    scripts = [
        "test_rga_search.py",
        "test_security_scan.py",
        "test_rga_allow_harness.py",
        "test_search_intent.py",
        "test_search_modes_harness.py",
        "test_capabilities.py",
        "schemas.py",
        "test_filereader.py",
    ]
    failed = 0
    for s in scripts:
        if _run(s) != 0:
            failed += 1
            print(f"[FAIL] {s}")
        else:
            print(f"[PASS] {s}")
    print("\n" + ("HARNESS FAILED" if failed else "HARNESS PASSED")
          + f"  ({len(scripts) - failed}/{len(scripts)} scripts green)")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
