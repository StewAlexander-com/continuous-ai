#!/usr/bin/env python3
"""Deterministic tests for search scope, names, quotes, and interpretation.

No live model. Live rg only for depth/quote/name filesystem cases.
Run: ./.venv/bin/python test_search_intent.py
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import rga_search as rs
import search_intent as si
import seedling


def test_parse_depth_name_quotes_intent():
    s = si.parse_search_spec("alpha in ~/Desktop depth 1")
    assert s.pattern == "alpha" and s.roots == ["~/Desktop"] and s.depth == 1
    s = si.parse_search_spec("foo depth all")
    assert s.pattern == "foo" and s.depth is None
    s = si.parse_search_spec('"Foo Bar"')
    assert s.quoted and s.exact and s.case == "sensitive_then_i" and s.pattern == "Foo Bar"
    s = si.parse_search_spec("name widget")
    assert s.match_kind == "both" and s.pattern == "widget" and not s.exact
    s = si.parse_search_spec("files named Widget.py")
    assert s.match_kind == "name" and s.pattern == "Widget.py"
    s = si.parse_search_spec("foo.*bar")
    assert s.exact is False and not s.needs_interpret
    s = si.parse_search_spec("I'm looking for any loops")
    assert s.needs_interpret and s.pattern.startswith("I'm looking")
    s = si.parse_search_spec("something in the logs")
    assert s.roots is None and s.needs_interpret
    s = si.parse_search_spec("SearchDenied")
    assert s.exact and not s.needs_interpret and s.match_kind == "content"
    s = si.parse_search_spec("retry logic")
    assert s.needs_interpret
    print("[PASS] parse: depth, name, quotes, intent vs token vs regex")


def test_looks_like_intent():
    assert si.looks_like_intent("I'm looking for any loops")
    assert si.looks_like_intent("any loops")
    assert si.looks_like_intent("retry logic")
    assert si.looks_like_intent("where do we set the timeout")
    assert si.looks_like_intent("what's the backoff")
    assert not si.looks_like_intent("SearchDenied")
    assert not si.looks_like_intent("foo.*bar")
    print("[PASS] intent heuristic: any English phrase, not only 'looking for loops'")


def test_interpret_loops_fake_chat():
    spec = si.parse_search_spec("I'm looking for any loops")
    assert spec.needs_interpret

    def chat_fn(messages, options):
        assert "JSON" in messages[0]["content"]
        return (
            '{"mode":"content","depth":null,"exact":true,'
            '"patterns":["for ","while ","foreach","do {"],'
            '"note":"loop constructs"}'
        )

    out = si.interpret_search_spec(spec, chat_fn=chat_fn)
    assert out.interpreted
    assert "while " in out.patterns
    assert "looking for" not in "".join(out.patterns)
    assert "loop" in out.interpret_note
    print("[PASS] interpret: loops → for/while needles, not the sentence")


def test_interpret_bad_json_falls_back():
    spec = si.parse_search_spec("find me the widgets please now")
    out = si.interpret_search_spec(spec, chat_fn=lambda m, o: "not json")
    assert not out.interpreted
    assert "JSON" in out.interpret_note or "not JSON" in out.interpret_note.lower()
    print("[PASS] interpret failure searches the words as typed")


def test_depth_one_skips_nested_content():
    if not rs.rg_binary():
        print("[SKIP] depth check needs rg")
        return
    d = Path(tempfile.mkdtemp(prefix="srch_d_"))
    nested = d / "a" / "b"
    nested.mkdir(parents=True)
    (d / "top.py").write_text("token-depth UNIQUE_TOP\n", encoding="utf-8")
    (nested / "deep.py").write_text("token-depth UNIQUE_DEEP\n", encoding="utf-8")
    try:
        shallow = rs.run_search(
            "UNIQUE_DEEP", enabled=True, allowed_paths=[str(d)],
            max_depth=1, exact=True, no_cache=True,
        )
        deep = rs.run_search(
            "UNIQUE_DEEP", enabled=True, allowed_paths=[str(d)],
            max_depth=3, exact=True, no_cache=True,
        )
        assert shallow.hits == [], shallow
        assert deep.hits, deep.message
        print("[PASS] depth 1 misses nested file; depth 3 finds it")
    finally:
        (nested / "deep.py").unlink()
        nested.rmdir()
        (d / "a").rmdir()
        (d / "top.py").unlink()
        d.rmdir()


def test_quoted_case_sensitive_then_insensitive():
    if not rs.rg_binary():
        print("[SKIP] quoted case check needs rg")
        return
    d = Path(tempfile.mkdtemp(prefix="srch_q_"))
    (d / "a.py").write_text("AlphaToken\nalphatoken\n", encoding="utf-8")
    try:
        result = rs.run_search(
            "AlphaToken", enabled=True, allowed_paths=[str(d)],
            exact=True, case="sensitive_then_i", no_cache=True,
        )
        assert len(result.hits) >= 2, result
        lines = {h.line for h in result.hits}
        assert 1 in lines and 2 in lines
        print("[PASS] quoted exact: case-sensitive hit plus case-insensitive extras")
    finally:
        (d / "a.py").unlink()
        d.rmdir()


def test_name_search_files_and_folders():
    if not (rs.rg_binary() or rs.rga_binary()):
        print("[SKIP] name search needs rg or rga")
        return
    d = Path(tempfile.mkdtemp(prefix="srch_n_"))
    (d / "widget_tool.py").write_text("x\n", encoding="utf-8")
    (d / "widget_dir").mkdir()
    (d / "other.py").write_text("x\n", encoding="utf-8")
    try:
        result = rs.run_search(
            "widget", enabled=True, allowed_paths=[str(d)],
            match_kind="both", exact=False, case="insensitive",
            max_depth=1, no_cache=True,
        )
        names = {Path(h.path).name for h in result.hits}
        assert "widget_tool.py" in names
        assert "widget_dir" in names
        assert "other.py" not in names
        print("[PASS] name search hits file and folder, not unrelated names")
    finally:
        (d / "widget_tool.py").unlink()
        (d / "widget_dir").rmdir()
        (d / "other.py").unlink()
        d.rmdir()


def test_interpret_arbitrary_english_fake_chat():
    spec = si.parse_search_spec("where do we retry failed calls")
    assert spec.needs_interpret

    def chat_fn(messages, options):
        assert "ANY natural-language" in messages[0]["content"]
        return (
            '{"mode":"content","depth":null,"exact":true,'
            '"patterns":["retry","backoff","except"],'
            '"note":"retry/backoff"}'
        )

    out = si.interpret_search_spec(spec, chat_fn=chat_fn)
    assert out.interpreted
    assert "retry" in out.patterns
    assert "where do we" not in "".join(out.patterns)
    print("[PASS] interpret: arbitrary English, not a closed phrase list")


def test_judge_fit_defaults_to_no_nag():
    spec = si.parse_search_spec("static global variables")
    spec.interpreted = True
    spec.patterns = ["static"]
    fit, try_ask = si.judge_search_fit(spec, [], chat_fn=lambda m, o: "not json")
    assert fit is True and try_ask is None
    fit, try_ask = si.judge_search_fit(
        spec, [], chat_fn=lambda m, o: '{"fit":false,"try":"UNIQUE_FILE_ONLY"}',
    )
    assert fit is False and try_ask == "UNIQUE_FILE_ONLY"
    print("[PASS] fit check nags only on clear mismatch with a usable try")


def test_spec_from_try_cannot_widen_or_echo():
    d = Path(tempfile.mkdtemp(prefix="srch_try_"))
    target = d / "text.txt"
    target.write_text("x\n", encoding="utf-8")
    try:
        spec = si.parse_search_spec(f"static global variables {target}")
        spec.interpreted = True
        spec.pattern = "static"
        spec.patterns = ["static"]
        alt = si.spec_from_try(spec, "UNIQUE_FILE_ONLY")
        assert alt is not None
        assert alt.file_only and alt.roots == spec.roots
        assert alt.pattern == "UNIQUE_FILE_ONLY"
        assert si.spec_from_try(spec, str(target)) is None
        assert si.spec_from_try(spec, "static global variables") is None
        assert si.spec_from_try(spec, ".") is None
        print("[PASS] retry spec stays file-only and drops echo/wildcards/paths")
    finally:
        target.unlink()
        d.rmdir()


def test_explicit_file_is_file_only():
    d = Path(tempfile.mkdtemp(prefix="srch_f_"))
    target = d / "text.txt"
    sibling = d / "other.txt"
    target.write_text("UNIQUE_FILE_ONLY\n", encoding="utf-8")
    sibling.write_text("UNIQUE_FILE_ONLY\n", encoding="utf-8")
    try:
        s = si.parse_search_spec(f"UNIQUE_FILE_ONLY {target}")
        assert s.file_only and s.roots == [str(target)]
        s2 = si.parse_search_spec(f"UNIQUE_FILE_ONLY in {target}")
        assert s2.file_only and s2.roots == [str(target)]
        s3 = si.parse_search_spec(f"{target} UNIQUE_FILE_ONLY")
        assert s3.file_only and s3.pattern == "UNIQUE_FILE_ONLY"
        if rs.rg_binary():
            result = rs.run_search(
                "UNIQUE_FILE_ONLY", enabled=True, allowed_paths=[str(d)],
                roots=[str(target)], exact=True, no_cache=True,
            )
            assert result.hits
            assert all(Path(h.path).name == "text.txt" for h in result.hits), result.hits
        print("[PASS] explicit /path/file.txt is that file only (not its siblings)")
    finally:
        target.unlink()
        sibling.unlink()
        d.rmdir()


def test_help_teaches_search():
    lines = "\n".join(si.format_help_lines())
    assert "file only" in lines
    assert "English" in lines
    assert "name <pat>" in lines
    assert "quoted" in lines or '"' in lines
    help_src = Path("seedling.py").read_text(encoding="utf-8")
    assert "format_help_lines" in help_src
    print("[PASS] :help and usage teach file-only, English, quotes, names")


def test_review_ask_requires_citations():
    spec = si.parse_search_spec("I'm looking for any loops")
    spec.interpreted = True
    spec.patterns = ["for ", "while "]
    spec.interpret_note = "loop constructs"
    text = si.format_search_ask(spec=spec)
    assert "path:line" in text
    assert "loops" in text.lower() or "looking" in text.lower()
    print("[PASS] review ask names the search and requires path:line")


def test_compose_staged_search_uses_search_ask():
    spec = si.parse_search_spec('"Alpha"')
    state = {
        "kind": "search",
        "name": "search results",
        "done": True,
        "staged": ["[hits]"],
        "search_spec": spec,
    }
    turn, submit = seedling._compose_staged_turn(state, "which file?")
    assert submit
    assert "which file?" in turn
    assert "path:line" in turn
    assert "attached" not in turn.lower()
    print("[PASS] follow-up on search hits uses the search review contract")


def test_handler_yes_path_still_stages_without_session():
    """Allow-harness contract: session=None still stages hits, never writes live config."""
    if not rs.rg_binary():
        print("[SKIP] handler stage check needs rg")
        return
    d = Path(tempfile.mkdtemp(prefix="srch_h_"))
    (d / "a.py").write_text("needle-xyz\n", encoding="utf-8")
    cfg = d / "config.yaml"
    cfg.write_text(
        "rga_search_enabled: true\n"
        f"rga_search_allowed_paths:\n  - {d}\n",
        encoding="utf-8",
    )
    config = {
        "rga_search_enabled": True,
        "rga_search_allowed_paths": [str(d)],
        "rga_search_max_hits": 10,
        "rga_search_timeout_s": 8,
        "rga_search_max_filesize": "4M",
    }
    state: dict = {}
    try:
        seedling._handle_search_command(
            None, f":search needle-xyz in {d}", config, state, config_path=cfg,
        )
        assert state.get("kind") == "search"
        assert "needle-xyz" in (state.get("text") or "")
        assert "How:" in (state.get("text") or "")
        print("[PASS] handler without session still stages reviewed search block")
    finally:
        (d / "a.py").unlink()
        cfg.unlink()
        d.rmdir()


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
