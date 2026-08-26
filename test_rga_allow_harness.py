#!/usr/bin/env python3
"""Harness: named-path guffaw → y/N → allowlist write → search/scan.

Never writes the live config.yaml. Isolated tempfile trees + injected answers.
Run: ./.venv/bin/python test_rga_allow_harness.py
"""
from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

import rga_search as rs
import seedling
import yaml

ROOT = Path(__file__).resolve().parent
LIVE_CONFIG = ROOT / "config.yaml"


def _fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _isolated() -> tuple[Path, Path, Path, dict]:
    """(workdir, yaml_path, extra_dir, config_dict). extra_dir is NOT allowlisted."""
    work = Path(tempfile.mkdtemp(prefix="rga_allow_h_"))
    home = work / "home"
    extra = work / "extra"
    home.mkdir()
    extra.mkdir()
    (home / "keep.txt").write_text("home-token-aaa\n", encoding="utf-8")
    (extra / "hit.txt").write_text("extra-token-zzz\nAKIAABCDEFGHIJKLMNOP\n", encoding="utf-8")
    cfg = work / "config.yaml"
    cfg.write_text(
        "# harness comment must survive\n"
        "rga_search_enabled: true\n"
        "rga_search_allowed_paths:\n"
        f"  - {home}\n"
        "security_scan_enabled: true\n",
        encoding="utf-8",
    )
    config = {
        "rga_search_enabled": True,
        "rga_search_allowed_paths": [str(home)],
        "security_scan_enabled": True,
        "rga_search_max_hits": 20,
        "rga_search_timeout_s": 8,
        "rga_search_max_filesize": "4M",
    }
    return work, cfg, extra, config


def _cleanup(work: Path) -> None:
    for dirpath, dirnames, filenames in os.walk(work, topdown=False):
        for name in filenames:
            Path(dirpath, name).unlink()
        for name in dirnames:
            Path(dirpath, name).rmdir()
    work.rmdir()


def test_yes_writes_yaml_and_search_finds_the_named_tree():
    if not (rs.rg_binary() or rs.rga_binary()):
        print("[SKIP] yes-path search needs rg or rga")
        return
    work, cfg, extra, config = _isolated()
    asked = []
    try:
        read_state: dict = {}
        seedling._handle_search_command(
            None,
            f":search extra-token-zzz in {extra}",
            config,
            read_state,
            config_path=cfg,
            ask=lambda p: asked.append(p) or True,
        )
        assert asked, "should ask before adding a new folder"
        # config.yaml keeps its comments AND stays pristine: the add goes to the
        # gitignored config.local.yaml, so using Aida cannot dirty the repo.
        import localconfig
        text = cfg.read_text(encoding="utf-8")
        assert "# harness comment must survive" in text
        assert str(extra.resolve()) not in text, "config.yaml must stay pristine"
        local = localconfig.local_path(cfg)
        assert local.is_file(), "the add must create config.local.yaml"
        assert str(extra.resolve()) in local.read_text(encoding="utf-8")
        # The live dict and the merged config both see it.
        assert str(extra.resolve()) in [str(p) for p in rs.expand_allowed(
            config.get("rga_search_allowed_paths")
        )]
        assert str(extra.resolve()) in [str(Path(p).resolve()) for p in
            (localconfig.load(cfg).get("rga_search_allowed_paths") or [])]
        assert read_state.get("kind") == "search", read_state
        assert "extra-token-zzz" in (read_state.get("text") or "")
        print("[PASS] y adds the folder to yaml and search hits the named tree")
    finally:
        _cleanup(work)


def test_no_leaves_yaml_and_does_not_search():
    work, cfg, extra, config = _isolated()
    before = cfg.read_text(encoding="utf-8")
    try:
        read_state: dict = {}
        seedling._handle_search_command(
            None,
            f":search extra-token-zzz in {extra}",
            config,
            read_state,
            config_path=cfg,
            ask=lambda p: False,
        )
        assert cfg.read_text(encoding="utf-8") == before
        assert extra.resolve() not in rs.expand_allowed(
            config.get("rga_search_allowed_paths")
        )
        assert read_state.get("kind") != "search"
        assert not read_state.get("text")
        print("[PASS] N leaves yaml alone and does not run the search")
    finally:
        _cleanup(work)


def test_missing_path_does_not_ask_or_write():
    work, cfg, extra, config = _isolated()
    before = cfg.read_text(encoding="utf-8")
    missing = work / "nope-not-here"
    asked = []
    try:
        ok = seedling._ensure_named_roots_allowed(
            config, [str(missing)], config_path=cfg, ask=lambda p: asked.append(p) or True,
        )
        assert ok is False
        assert asked == []
        assert cfg.read_text(encoding="utf-8") == before
        print("[PASS] missing path is not added (no prompt)")
    finally:
        _cleanup(work)


def test_already_allowlisted_does_not_ask():
    work, cfg, extra, config = _isolated()
    asked = []
    try:
        home = rs.expand_allowed(config["rga_search_allowed_paths"])[0]
        ok = seedling._ensure_named_roots_allowed(
            config, [str(home)], config_path=cfg, ask=lambda p: asked.append(p) or True,
        )
        assert ok is True
        assert asked == []
        print("[PASS] already-allowlisted path does not prompt")
    finally:
        _cleanup(work)


def test_allow_drop_undoes_a_yes():
    work, cfg, extra, config = _isolated()
    try:
        seedling._handle_allow_command(
            config, f":allow {extra}", config_path=cfg, ask=lambda p: True,
        )
        assert extra.resolve() in rs.expand_allowed(config["rga_search_allowed_paths"])
        seedling._handle_allow_command(
            config, ":allow drop 2", config_path=cfg,
        )
        assert extra.resolve() not in rs.expand_allowed(
            config.get("rga_search_allowed_paths") or []
        )
        data = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
        assert extra.resolve() not in rs.expand_allowed(data.get("rga_search_allowed_paths") or [])
        print("[PASS] :allow drop undoes a confirmed add")
    finally:
        _cleanup(work)


def test_scan_yes_scopes_to_the_new_folder():
    if not (rs.rg_binary() or rs.rga_binary()):
        print("[SKIP] scan-yes needs rg or rga")
        return
    work, cfg, extra, config = _isolated()
    try:
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            seedling._handle_scan_command(
                config, f":scan {extra}", config_path=cfg, ask=lambda p: True,
            )
        out = buf.getvalue()
        # The add must land in the gitignored override, and the tracked
        # config.yaml must be left alone — using Aida cannot dirty the repo.
        import localconfig
        local = localconfig.local_path(cfg)
        assert local.is_file(), "an allowlist add must create config.local.yaml"
        assert str(extra.resolve()) in local.read_text(encoding="utf-8"), (
            f"path missing from {local.name}"
        )
        assert str(extra.resolve()) not in cfg.read_text(encoding="utf-8"), (
            "config.yaml must stay pristine"
        )
        # ...and the effective config still sees it, so the scan is scoped.
        assert str(extra.resolve()) in [
            str(Path(p).resolve()) for p in
            (localconfig.load(cfg).get("rga_search_allowed_paths") or [])
        ], "the merged config must contain the new folder"
        assert "aws_access_key" in out or "AKIA" in out
        print("[PASS] :scan y on a new folder writes the list and reports the finding")
    finally:
        _cleanup(work)


def main() -> int:
    live_before = _fingerprint(LIVE_CONFIG)
    tests = [
        test_yes_writes_yaml_and_search_finds_the_named_tree,
        test_no_leaves_yaml_and_does_not_search,
        test_missing_path_does_not_ask_or_write,
        test_already_allowlisted_does_not_ask,
        test_allow_drop_undoes_a_yes,
        test_scan_yes_scopes_to_the_new_folder,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except Exception as e:
            failed += 1
            print(f"[FAIL] {t.__name__}: {e}")
    live_after = _fingerprint(LIVE_CONFIG)
    if live_before != live_after:
        failed += 1
        print("[FAIL] live config.yaml was modified — harness must not touch it")
    else:
        print("[PASS] live config.yaml unchanged")
    total = len(tests) + 1
    print(f"\n{total - failed}/{total} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
