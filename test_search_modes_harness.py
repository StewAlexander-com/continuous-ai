#!/usr/bin/env python3
"""Comprehensive harness for :search modes (scope, names, quotes, English, review).

Never writes the live config.yaml. Isolated tempfile trees. Fake session
(no live model) for interpret + review. Live rg for filesystem cases.

Run: ./.venv/bin/python test_search_modes_harness.py
"""
from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

import rga_search as rs
import search_intent as si
import seedling

ROOT = Path(__file__).resolve().parent
LIVE_CONFIG = ROOT / "config.yaml"


def _fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree() -> tuple[Path, Path, Path, Path]:
    """workdir, home (allowlisted), extra file, extra sibling."""
    work = Path(tempfile.mkdtemp(prefix="srch_modes_"))
    home = work / "home"
    home.mkdir()
    nested = home / "sub" / "deep"
    nested.mkdir(parents=True)
    (home / "top.py").write_text("TOKEN_TOP = 1\nAlphaToken\n", encoding="utf-8")
    (nested / "deep.py").write_text("TOKEN_DEEP = 1\n", encoding="utf-8")
    (home / "widget_tool.py").write_text("x\n", encoding="utf-8")
    (home / "widget_dir").mkdir()
    target = home / "text.txt"
    sibling = home / "other.txt"
    target.write_text("UNIQUE_FILE_ONLY\nretry failed calls\n", encoding="utf-8")
    sibling.write_text("UNIQUE_FILE_ONLY\n", encoding="utf-8")
    return work, home, target, sibling


def _cleanup(work: Path) -> None:
    for dirpath, dirnames, filenames in os.walk(work, topdown=False):
        for name in filenames:
            Path(dirpath, name).unlink(missing_ok=True)
        for name in dirnames:
            Path(dirpath, name).rmdir()
    work.rmdir()


def _cfg(home: Path) -> tuple[Path, dict]:
    cfg = home.parent / "config.yaml"
    cfg.write_text(
        "rga_search_enabled: true\n"
        f"rga_search_allowed_paths:\n  - {home}\n",
        encoding="utf-8",
    )
    config = {
        "rga_search_enabled": True,
        "rga_search_allowed_paths": [str(home)],
        "rga_search_max_hits": 20,
        "rga_search_timeout_s": 8,
        "rga_search_max_filesize": "4M",
    }
    return cfg, config


class FakeSession:
    """Interpret/fit via _chat_once; review via chat. Never touches memory."""

    def __init__(
        self,
        interpret_json: str = '{"patterns":["retry"],"note":"ok"}',
        fit_json: str = '{"fit":true,"try":null}',
        retry_json: str | None = None,
    ):
        self.model_name = "fake"
        self.interpret_json = interpret_json
        self.fit_json = fit_json
        self.retry_json = retry_json or interpret_json
        self.interpret_calls = 0
        self.fit_calls = 0
        self.review_turns: list[str] = []

    def _chat_once(self, model, messages, options=None, think=None):
        sys = ""
        if messages:
            sys = str(messages[0].get("content") or "")
        if "Judge whether these search hits" in sys:
            self.fit_calls += 1
            return self.fit_json
        self.interpret_calls += 1
        if self.interpret_calls == 1:
            return self.interpret_json
        return self.retry_json

    def chat(self, turn_text, on_token=None):
        self.review_turns.append(turn_text)
        if on_token:
            on_token("ok")
        return "ok"


# --- parse / intent --------------------------------------------------------

def test_english_is_any_phrase_not_a_phrase_list():
    for ask in (
        "I'm looking for any loops",
        "retry logic",
        "where do we set the timeout",
        "what's the backoff",
        "show connection errors",
        "failed calls",
    ):
        assert si.looks_like_intent(ask), ask
        assert si.parse_search_spec(ask).needs_interpret, ask
    assert not si.looks_like_intent("SearchDenied")
    assert not si.parse_search_spec("SearchDenied").needs_interpret
    print("[PASS] any English phrase interprets; a token does not")


def test_quoted_in_file_keeps_quotes():
    work, home, target, _ = _tree()
    try:
        s = si.parse_search_spec(f'"AlphaToken" in {target}')
        assert s.quoted, s
        assert s.file_only and s.roots == [str(target)]
        assert s.pattern == "AlphaToken"
        assert s.case == "sensitive_then_i"
        assert not s.needs_interpret
        print("[PASS] :search \"exact\" in /file.txt keeps quoted exact + file-only")
    finally:
        _cleanup(work)


def test_english_plus_file_stays_file_only():
    work, home, target, _ = _tree()
    try:
        s = si.parse_search_spec(f"retry logic {target}")
        assert s.needs_interpret and s.file_only
        assert s.pattern == "retry logic"
        s2 = si.parse_search_spec(f"{target} where is the timeout")
        assert s2.file_only and s2.needs_interpret
        named = si.parse_search_spec(f"name widget {target}")
        assert named.file_only and named.match_kind == "content"
        print("[PASS] English + /path/file.txt is that file only, still interpreted")
    finally:
        _cleanup(work)


def test_bare_file_path_is_not_a_content_needle():
    work, home, target, _ = _tree()
    try:
        s = si.parse_search_spec(str(target))
        assert s.file_only
        assert not s.pattern, s
        cfg, config = _cfg(home)
        state: dict = {}
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            seedling._handle_search_command(
                None, f":search {target}", config, state, config_path=cfg,
            )
        out = buf.getvalue()
        assert state.get("kind") != "search"
        assert "look for in that file" in out
        assert str(target) not in (state.get("text") or "")
        print("[PASS] bare /path/file.txt asks for a pattern, does not search the path string")
    finally:
        _cleanup(work)


def test_in_the_logs_is_not_a_path():
    s = si.parse_search_spec("something in the logs")
    assert s.roots is None and s.needs_interpret
    print("[PASS] English 'in the logs' is not a path")


def test_bare_folder_is_not_a_content_needle():
    work, home, _, _ = _tree()
    try:
        s = si.parse_search_spec(str(home))
        assert s.roots == [str(home)]
        assert not s.pattern
        assert not s.file_only
        cfg, config = _cfg(home)
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            seedling._handle_search_command(
                None, f":search {home}", config, {}, config_path=cfg,
            )
        assert "look for in that folder" in buf.getvalue()
        print("[PASS] bare /path/dir asks for a pattern, does not search the path string")
    finally:
        _cleanup(work)


def test_missing_file_is_named_clearly():
    work, home, _, _ = _tree()
    missing = home / "nope.txt"
    cfg, config = _cfg(home)
    try:
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            seedling._handle_search_command(
                None, f":search retry {missing}", config, {}, config_path=cfg,
            )
        out = buf.getvalue()
        assert "does not exist" in out.lower()
        assert "nope.txt" in out
        print("[PASS] missing named file is an honest error, not a search")
    finally:
        _cleanup(work)


def test_interpret_fail_is_honest():
    work, home, target, _ = _tree()
    cfg, config = _cfg(home)
    session = FakeSession("not json")
    state: dict = {}
    try:
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            seedling._handle_search_command(
                session, f":search where is the unique token {target}",
                config, state, config_path=cfg,
            )
        how = buf.getvalue() + (state.get("text") or "")
        assert "not JSON" in how or "not json" in how.lower()
        assert "words as typed" in how
        print("[PASS] interpret failure is named; search still runs the words as typed")
    finally:
        _cleanup(work)


# --- interpret -------------------------------------------------------------

def test_interpret_drops_the_sentence_and_wildcards():
    spec = si.parse_search_spec("where do we retry failed calls")

    def chat_fn(messages, options):
        return (
            '{"mode":"content","exact":true,'
            '"patterns":["where do we retry failed calls",".*",".","retry","backoff"],'
            '"note":"retry"}'
        )

    out = si.interpret_search_spec(spec, chat_fn=chat_fn)
    assert out.interpreted
    joined = " ".join(out.patterns)
    assert "where do we" not in joined
    assert ".*" not in out.patterns and "." not in out.patterns
    assert "retry" in out.patterns
    print("[PASS] interpreter cannot use the sentence or .* as a needle")


def test_interpret_cannot_widen_a_file():
    work, home, target, _ = _tree()
    try:
        spec = si.parse_search_spec(f"retry logic {target}")
        assert spec.file_only

        def chat_fn(messages, options):
            return '{"mode":"name","depth":3,"patterns":["retry"],"note":"x"}'

        out = si.interpret_search_spec(spec, chat_fn=chat_fn)
        assert out.file_only and out.roots == spec.roots
        assert out.depth is None
        assert out.match_kind == "content"
        print("[PASS] interpret cannot widen a file-only search into a tree")
    finally:
        _cleanup(work)


def test_handler_english_reviews_interpreted_hits():
    work, home, target, sibling = _tree()
    cfg, config = _cfg(home)
    session = FakeSession(
        '{"patterns":["UNIQUE_FILE_ONLY"],"note":"unique token"}'
    )
    state: dict = {}
    try:
        seedling._handle_search_command(
            session, f":search where is the unique token {target}",
            config, state, config_path=cfg,
        )
        assert session.interpret_calls == 1
        assert session.fit_calls == 1
        assert session.review_turns, "review turn must run"
        review = session.review_turns[0]
        assert "UNIQUE_FILE_ONLY" in review
        assert "path:line" in review
        assert "other.txt" not in review
        assert "text.txt" in review
        print("[PASS] handler: English → interpret → file-only hits → review")
    finally:
        _cleanup(work)


def test_token_search_skips_fit_gate():
    if not rs.rg_binary():
        print("[SKIP] token fit-skip needs rg")
        return
    work, home, target, _ = _tree()
    cfg, config = _cfg(home)
    session = FakeSession(fit_json='{"fit":false,"try":"nope"}')
    state: dict = {}
    asked = []
    try:
        seedling._handle_search_command(
            session, f":search UNIQUE_FILE_ONLY {target}",
            config, state, config_path=cfg, ask=lambda p: asked.append(p) or True,
        )
        assert session.interpret_calls == 0
        assert session.fit_calls == 0
        assert not asked
        assert session.review_turns
        print("[PASS] token search does not fit-check or ask did-you-mean")
    finally:
        _cleanup(work)


def test_mismatch_yes_retries_same_file():
    if not rs.rg_binary():
        print("[SKIP] mismatch retry needs rg")
        return
    work, home, target, sibling = _tree()
    cfg, config = _cfg(home)
    session = FakeSession(
        interpret_json='{"patterns":["NO_SUCH_NEEDLE_XYZ"],"note":"static"}',
        fit_json='{"fit":false,"try":"UNIQUE_FILE_ONLY"}',
    )
    state: dict = {}
    try:
        seedling._handle_search_command(
            session, f":search static global variables {target}",
            config, state, config_path=cfg, ask=lambda p: True,
        )
        assert session.fit_calls == 1
        assert session.review_turns
        review = session.review_turns[0]
        assert "UNIQUE_FILE_ONLY" in review
        assert "other.txt" not in review
        assert "retried" in review.lower() or "UNIQUE_FILE_ONLY" in review
        print("[PASS] mismatch + y: smoke, ask, retry same file, then review")
    finally:
        _cleanup(work)


def test_mismatch_no_keeps_first_search():
    if not rs.rg_binary():
        print("[SKIP] mismatch N needs rg")
        return
    work, home, target, _ = _tree()
    cfg, config = _cfg(home)
    session = FakeSession(
        interpret_json='{"patterns":["NO_SUCH_NEEDLE_XYZ"],"note":"static"}',
        fit_json='{"fit":false,"try":"UNIQUE_FILE_ONLY"}',
    )
    state: dict = {}
    try:
        seedling._handle_search_command(
            session, f":search static global variables {target}",
            config, state, config_path=cfg, ask=lambda p: False,
        )
        assert session.fit_calls == 1
        review = session.review_turns[0]
        assert "UNIQUE_FILE_ONLY" not in review
        print("[PASS] mismatch + N: keep first search, still review")
    finally:
        _cleanup(work)


# --- filesystem ------------------------------------------------------------

def test_file_only_does_not_hit_sibling():
    if not rs.rg_binary():
        print("[SKIP] file-only live check needs rg")
        return
    work, home, target, sibling = _tree()
    try:
        result = rs.run_search(
            "UNIQUE_FILE_ONLY", enabled=True, allowed_paths=[str(home)],
            roots=[str(target)], exact=True, no_cache=True,
        )
        assert result.hits
        names = {Path(h.path).name for h in result.hits}
        assert names == {"text.txt"}, names
        print("[PASS] live rg: explicit file does not include sibling")
    finally:
        _cleanup(work)


def test_depth_and_names_and_quotes_live():
    if not rs.rg_binary():
        print("[SKIP] live depth/name/quote needs rg")
        return
    work, home, target, _ = _tree()
    try:
        shallow = rs.run_search(
            "TOKEN_DEEP", enabled=True, allowed_paths=[str(home)],
            max_depth=1, exact=True, no_cache=True,
        )
        deep = rs.run_search(
            "TOKEN_DEEP", enabled=True, allowed_paths=[str(home)],
            max_depth=3, exact=True, no_cache=True,
        )
        assert not shallow.hits and deep.hits
        quoted = rs.run_search(
            "AlphaToken", enabled=True, allowed_paths=[str(home)],
            exact=True, case="sensitive_then_i", no_cache=True,
        )
        assert quoted.hits
        names = rs.run_search(
            "widget", enabled=True, allowed_paths=[str(home)],
            match_kind="both", exact=False, case="insensitive",
            max_depth=1, no_cache=True,
        )
        found = {Path(h.path).name for h in names.hits}
        assert "widget_tool.py" in found and "widget_dir" in found
        print("[PASS] live: depth 1 vs 3, quoted exact, name files+folders")
    finally:
        _cleanup(work)


def test_help_and_bare_search_usage():
    text = "\n".join(si.format_help_lines())
    for needle in ("file only", "English", "name <pat>", "quoted", "depth"):
        assert needle.lower() in text.lower() or (
            needle == "quoted" and '"' in text
        ), needle
    first = si.format_help_lines()[0]
    assert "interprets" in first and "reviews" in first
    assert "token =" not in text
    work, home, _, _ = _tree()
    cfg, config = _cfg(home)
    try:
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            seedling._handle_search_command(None, ":search", config, {}, config_path=cfg)
        out = buf.getvalue()
        assert "English" in out and "file only" in out
        assert "interprets" in out and "reviews" in out
        print("[PASS] :help and bare :search teach the modes")
    finally:
        _cleanup(work)


def test_search_enhances_aida_not_rg():
    """Product lock: Aida understands and reviews; rg is the engine."""
    src = Path(__file__).resolve().parent.joinpath("search_intent.py").read_text(encoding="utf-8")
    handler = Path(__file__).resolve().parent.joinpath("seedling.py").read_text(encoding="utf-8")
    rga = Path(__file__).resolve().parent.joinpath("rga_search.py").read_text(encoding="utf-8")
    assert "not compiling a regex" in si.INTERPRET_SYS
    assert "never session.chat" in src
    assert "deliberation_ledger" in src  # named only as a forbidden path
    assert "Judge whether these search hits" in si.FIT_SYS
    assert "session._chat_once" in handler
    assert "_stream_turn(session, block" in handler
    assert "subprocess" in rga
    help0 = si.format_help_lines()[0]
    assert help0.index("interprets") < help0.index("reviews")
    print("[PASS] product path is Aida interpret → search → review; rg is the engine")


def main() -> int:
    live_before = _fingerprint(LIVE_CONFIG)
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
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
        print("[FAIL] live config.yaml was modified")
    else:
        print("[PASS] live config.yaml unchanged")
    total = len(tests) + 1
    print(f"\n{total - failed}/{total} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
