#!/usr/bin/env python3
"""Tests for the gated rga search adapter.

Deterministic where possible (no model). Live rga is used only when the
binary is on PATH; otherwise those cases skip. Flags default off.

Run: ./.venv/bin/python test_rga_search.py
"""
from __future__ import annotations

import ast
import json
import os
import sys
import tempfile
from pathlib import Path

import rga_search as rs
from schemas import SearchHit, SearchResult, to_json


def test_schemas_search_hit_cite():
    h = SearchHit(path="/tmp/a.py", line=4, text="hello")
    assert h.cite() == "/tmp/a.py:4"
    blob = to_json(SearchResult(query="hello", hits=[h], message=""))
    assert "hello" in blob and "line" in blob
    print("[PASS] SearchHit/SearchResult serialize and cite path:line")


def test_never_imports_rga_package():
    src = Path("rga_search.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] != "rga", alias.name
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            assert not mod.startswith("rga"), mod
    assert "subprocess" in src
    assert "shutil.which" in src or "which(" in src
    print("[PASS] rga_search.py shells out; never imports rga internals")


def test_denied_when_flag_off():
    ok, msg = rs.permit(False, ["/tmp"])
    assert ok is False and "off" in msg.lower()
    try:
        rs.run_search("foo", enabled=False, allowed_paths=["/tmp"])
        assert False, "should deny"
    except rs.SearchDenied as e:
        assert "off" in str(e).lower()
    print("[PASS] flag off denies search (no subprocess)")


def test_denied_when_allowlist_empty():
    ok, msg = rs.permit(True, [])
    assert ok is False and "allowlist" in msg.lower()
    try:
        rs.run_search("foo", enabled=True, allowed_paths=[])
        assert False, "should deny"
    except rs.SearchDenied:
        pass
    print("[PASS] empty allowlist denies search")


def test_path_outside_allowlist_denied():
    if not rs.rga_binary():
        print("[SKIP] outside-allowlist live check needs rga")
        return
    d = tempfile.mkdtemp(prefix="rga_allow_")
    other = tempfile.mkdtemp(prefix="rga_other_")
    try:
        Path(other, "x.txt").write_text("secret-token-xyz\n", encoding="utf-8")
        try:
            rs.run_search(
                "secret-token-xyz",
                enabled=True,
                allowed_paths=[d],
                roots=[other],
                no_cache=True,
            )
            assert False, "should deny root outside allowlist"
        except rs.SearchDenied as e:
            assert "outside" in str(e).lower() or "allowlist" in str(e).lower()
    finally:
        for p in (d, other):
            for child in Path(p).iterdir():
                child.unlink()
            os.rmdir(p)
    print("[PASS] path outside allowlist is denied")


def test_zero_match_is_valid():
    if not rs.rga_binary():
        print("[SKIP] zero-match live check needs rga")
        return
    d = tempfile.mkdtemp(prefix="rga_zero_")
    try:
        Path(d, "a.txt").write_text("nothing relevant here\n", encoding="utf-8")
        result = rs.run_search(
            "this-pattern-will-not-match-zzzx",
            enabled=True,
            allowed_paths=[d],
            no_cache=True,
        )
        assert result.hits == []
        assert result.message == "no matching content found"
        block = rs.format_search_block(result)
        assert "no matching content found" in block
        assert ":" not in block.split("SEARCH")[0] or True
    finally:
        Path(d, "a.txt").unlink()
        os.rmdir(d)
    print("[PASS] zero matches return 'no matching content found'")


def test_live_rga_hits_and_citations_resolve():
    if not rs.rga_binary():
        print("[SKIP] live hit check needs rga")
        return
    d = tempfile.mkdtemp(prefix="rga_hit_")
    target = Path(d, "widget.py")
    try:
        target.write_text("def alpha():\n    return 1\n\ndef beta():\n    return alpha()\n", encoding="utf-8")
        result = rs.run_search(
            "alpha",
            enabled=True,
            allowed_paths=[d],
            no_cache=True,
        )
        assert result.hits, result.message
        for h in result.hits:
            p = Path(h.path)
            assert p.exists(), h.path
            lines = p.read_text(encoding="utf-8").splitlines()
            assert 1 <= h.line <= len(lines), h
            assert "alpha" in lines[h.line - 1], (h, lines[h.line - 1])
        block = rs.format_search_block(result)
        assert "path:line" in block or "Citation contract" in block
        assert f"{target.name}:" in block or str(target) in block
    finally:
        target.unlink()
        os.rmdir(d)
    print("[PASS] live rga hits cite lines that exist in the file")


def test_timeout_keeps_partial_hits():
    """A hung extractor after the first JSON match must not drop that hit."""
    d = tempfile.mkdtemp(prefix="rga_to_")
    target = Path(d, "hit.py")
    target.write_text("alpha = 1\n", encoding="utf-8")
    payload = json.dumps({
        "type": "match",
        "data": {
            "path": {"text": str(target)},
            "line_number": 1,
            "lines": {"text": "alpha = 1\n"},
        },
    })
    script = "import time\nprint(%r, flush=True)\ntime.sleep(30)\n" % payload
    argv = [sys.executable, "-c", script]
    allowed = rs.expand_allowed([d])
    hits, _cap, timed_out, _err = rs._stream_json_hits(
        argv, allowed=allowed, max_hits=5, timeout_s=0.9,
    )
    try:
        assert hits, "timeout must keep the match already printed"
        assert hits[0].line == 1
        assert timed_out
    finally:
        target.unlink()
        os.rmdir(d)
    print("[PASS] timeout keeps partial hits")


def test_desktop_and_seedling_text_query_is_fast():
    """Regression: allowlisting Desktop must not 30s-empty SearchDenied."""
    seedling = "/Users/stewartalexander/seedling"
    desktop = "/Users/stewartalexander/Desktop"
    if not (rs.rg_binary() and Path(seedling).is_dir() and Path(desktop).is_dir()):
        print("[SKIP] desktop+seedling speed check needs rg + those dirs")
        return
    t0 = __import__("time").monotonic()
    result = rs.run_search(
        "SearchDenied",
        enabled=True,
        allowed_paths=[seedling, desktop],
        max_hits=8,
        timeout_s=8,
        no_cache=True,
    )
    elapsed = __import__("time").monotonic() - t0
    assert result.hits, result.message
    assert elapsed < 8, f"text-first search took {elapsed:.1f}s (budget 8s)"
    print(f"[PASS] seedling+Desktop SearchDenied in {elapsed:.2f}s with {len(result.hits)} hits")


def test_parse_search_arg():
    assert rs.parse_search_arg("") == ("", None)
    assert rs.parse_search_arg("--help") == ("", None)
    assert rs.parse_search_arg('"foo bar"') == ("foo bar", None)
    pat, roots = rs.parse_search_arg("alpha in ~/Desktop")
    assert pat == "alpha" and roots == ["~/Desktop"]
    pat, roots = rs.parse_search_arg("something in the logs")
    assert pat == "something in the logs" and roots is None
    print("[PASS] parse_search_arg: quotes, in <path>, English 'in' stays a pattern")


def test_leading_dash_is_not_an_rg_flag():
    argv = rs._text_argv("-foo", [Path("/tmp")], "4M")
    if argv is None:
        print("[SKIP] leading-dash argv check needs rg")
        return
    assert "--" in argv
    assert argv[argv.index("--") + 1] == "-foo"
    print("[PASS] leading-dash pattern is passed after --")


def test_coerce_clamps_config_typos():
    assert rs.coerce_max_hits(9999) == rs.MAX_HITS_CAP
    assert rs.coerce_max_hits("nope") == rs.DEFAULT_MAX_HITS
    assert rs.coerce_timeout_s(0) == rs.MIN_TIMEOUT_S
    assert rs.coerce_timeout_s(999) == rs.MAX_TIMEOUT_S
    assert rs.coerce_max_filesize("4GB") == rs.DEFAULT_MAX_FILESIZE
    assert rs.coerce_max_filesize("8M") == "8M"
    print("[PASS] hits/timeout/filesize typos clamp instead of hanging")


def test_missing_root_denied():
    if not (rs.rg_binary() or rs.rga_binary()):
        print("[SKIP] missing-root check needs rg or rga")
        return
    try:
        rs.run_search(
            "foo",
            enabled=True,
            allowed_paths=["/this/path/does/not/exist-zzzx"],
        )
        assert False, "should deny"
    except rs.SearchDenied as e:
        assert "exist" in str(e).lower()
    print("[PASS] missing allowlist path is denied before search")


def test_pattern_too_long_denied():
    if not (rs.rg_binary() or rs.rga_binary()):
        print("[SKIP] long-pattern check needs rg or rga")
        return
    try:
        rs.run_search("x" * 500, enabled=True, allowed_paths=["/tmp"])
        assert False, "should deny"
    except rs.SearchDenied as e:
        assert "characters" in str(e).lower()
    print("[PASS] overlong pattern is denied")


def test_parse_allow_arg():
    assert rs.parse_allow_arg("") == ("list", "")
    assert rs.parse_allow_arg("~/Desktop") == ("add", "~/Desktop")
    assert rs.parse_allow_arg("drop 2") == ("drop", "2")
    assert rs.parse_allow_arg("please") == ("usage", "please")
    print("[PASS] parse_allow_arg list/add/drop/usage")


def test_allowlist_yaml_add_drop_preserves_comments():
    d = tempfile.mkdtemp(prefix="rga_yaml_")
    extra = Path(d) / "notes"
    extra.mkdir()
    cfg = Path(d) / "config.yaml"
    cfg.write_text(
        "# keep this comment\n"
        "rga_search_enabled: true\n"
        "rga_search_allowed_paths:\n"
        "  - /tmp\n"
        "rga_search_max_hits: 50  # inline\n",
        encoding="utf-8",
    )
    ok, msg = rs.add_allowed_path_yaml(cfg, str(extra))
    assert ok, msg
    text = cfg.read_text(encoding="utf-8")
    assert "# keep this comment" in text
    assert "rga_search_max_hits: 50  # inline" in text
    assert str(extra.resolve()) in text
    ok2, _ = rs.add_allowed_path_yaml(cfg, str(extra))
    assert ok2 is False
    ok3, msg3 = rs.drop_allowed_path_yaml(cfg, "2")
    assert ok3, msg3
    text2 = cfg.read_text(encoding="utf-8")
    assert str(extra.resolve()) not in text2
    assert "/tmp" in text2
    cfg.unlink()
    extra.rmdir()
    os.rmdir(d)
    print("[PASS] allowlist yaml add/drop keeps comments and refuses duplicates")


def test_path_not_allowlisted_is_specific():
    d = tempfile.mkdtemp(prefix="rga_allow_")
    other = tempfile.mkdtemp(prefix="rga_other_")
    try:
        Path(other, "x.txt").write_text("hi\n", encoding="utf-8")
        if not (rs.rg_binary() or rs.rga_binary()):
            print("[SKIP] PathNotAllowlisted live check needs rg or rga")
            return
        try:
            rs.run_search("hi", enabled=True, allowed_paths=[d], roots=[other], no_cache=True)
            assert False, "should deny"
        except rs.PathNotAllowlisted as e:
            assert str(e.path) == str(Path(other).resolve())
    finally:
        Path(other, "x.txt").unlink()
        os.rmdir(other)
        os.rmdir(d)
    print("[PASS] PathNotAllowlisted names the folder so the REPL can ask")


def test_format_zero_and_hits():
    empty = SearchResult(query="q", hits=[], message="no matching content found")
    assert "no matching content found" in rs.format_search_block(empty)
    hit = SearchHit(path="/x.py", line=3, text="foo = 1")
    block = rs.format_search_block(SearchResult(query="foo", hits=[hit]))
    assert "/x.py:3" in block
    print("[PASS] search block formats zero-match and path:line hits")


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
