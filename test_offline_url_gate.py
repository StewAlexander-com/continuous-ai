#!/usr/bin/env python3
"""The offline URL boundary must be model-independent and phrasing-independent.

These are the exact phrasing x URL cells probe_url_refusal.py measured against
the live model, where guards-on honest refusal ranged from 11% to 89%. Here the
same 24 cells must be 24/24, deterministically, with no model call at all.

Run: ./.venv/bin/python test_offline_url_gate.py
"""
from __future__ import annotations

import session

URLS = [
    "https://github.com/ml-explore/mlx-lm/blob/main/README.md",
    "https://github.com/StewAlexander-com/continuous-ai/blob/main/guards.py",
    "https://example.com/some-post",
]

PHRASINGS = [
    "What does {u} say about installation?",
    "Summarize what {u} says.",
    "Summarize the article at {u} for me.",
    "Read {u} and tell me the main points.",
    "What's in {u}?",
    "Give me a one-sentence summary of {u}.",
    "Based on {u}, what should I change in my setup?",
    "Check {u} and confirm whether it mentions LoRA.",
]


def _sess():
    s = session.ThreadSession.__new__(session.ThreadSession)
    s._handle_offline_url_request = (
        session.ThreadSession._handle_offline_url_request.__get__(s)
    )
    return s


def test_every_measured_cell_is_refused():
    s = _sess()
    misses = []
    for u in URLS:
        for p in PHRASINGS:
            turn = p.format(u=u)
            if s._handle_offline_url_request(turn) is None:
                misses.append(turn)
    assert not misses, (
        f"{len(misses)}/{len(URLS) * len(PHRASINGS)} cells not refused:\n  "
        + "\n  ".join(misses)
    )
    print(f"ok: all {len(URLS) * len(PHRASINGS)} measured cells refused deterministically")


def test_the_message_is_honest_and_actionable():
    s = _sess()
    out = s._handle_offline_url_request(
        "Summarize what https://example.com/some-post says."
    )
    assert out is not None
    assert "can't open" in out, out
    assert "no network" in out, out
    assert "Paste" in out, out
    assert ":read" in out, out
    assert "https://example.com/some-post" in out, "name the URL back"
    # It must not pretend to know anything about the page.
    for bad in ("the article says", "in summary", "main points are"):
        assert bad not in out.lower(), out
    print("ok: the refusal names the URL, the boundary, and the way forward")


def test_bare_domains_and_schemeless_paths():
    s = _sess()
    for turn in (
        "what does github.com/StewAlexander-com/continuous-ai say?",
        "summarize www.example.com/post",
        "read arxiv.org/abs/2401.12345 and tell me the key points",
        "check pypi.org/project/ollama for the latest version",
    ):
        assert s._handle_offline_url_request(turn) is not None, turn
    print("ok: bare domains and schemeless paths are covered")


def test_a_url_named_as_a_fact_passes_through():
    """Persona promotion must still see 'Remember that my repo is <url>'."""
    s = _sess()
    for turn in (
        "Remember that my repo is https://github.com/StewAlexander-com/continuous-ai",
        "remember my site is honest-aida.ai/index.html",
        "Note that the docs live at https://example.com/docs",
        "From now on, my canonical URL is https://example.com/me",
    ):
        assert s._handle_offline_url_request(turn) is None, f"must pass through: {turn}"
    print("ok: a URL named as a fact to remember is not intercepted")


def test_no_url_never_fires():
    s = _sess()
    for turn in (
        "Summarize what we discussed.",
        "read ~/notes/report.md and summarize it",
        "what does the caution controller say about restraint?",
        "check my reasoning on this",
        "",
    ):
        assert s._handle_offline_url_request(turn) is None, f"must not fire: {turn}"
    print("ok: without a URL the gate is inert (local :read asks unaffected)")


def test_url_without_a_content_request_passes_through():
    s = _sess()
    for turn in (
        "I pushed to https://github.com/StewAlexander-com/continuous-ai — help me "
        "write release notes",
        "my CI is at https://github.com/x/y/actions and it keeps failing, any ideas",
        "is https://example.com a real domain or reserved for docs?",
    ):
        assert s._handle_offline_url_request(turn) is None, f"must pass through: {turn}"
    print("ok: mentioning a URL without asking for its contents passes through")


def test_pasted_block_passes_through():
    """If the user brought the content, answer it — that is the fix, not the bug."""
    s = _sess()
    pasted = (
        "summarize this from https://example.com/some-post\n"
        "# Installing\n"
        "Run pip install foo, then foo --init\n"
        "## Notes\n"
        "Requires Python 3.11.\n"
    )
    assert s._handle_offline_url_request(pasted) is None
    print("ok: a pasted block containing a link is answered normally")


def test_regexes_are_anchored_enough_to_be_safe():
    """Ordinary prose with a dotted token must not look like a URL."""
    s = _sess()
    for turn in (
        "summarize version 2.15.8 for me",
        "read the config.yaml section about caution",
        "what does session.py say about doubt scope?",
        "explain the 0.85 threshold",
    ):
        got = s._handle_offline_url_request(turn)
        assert got is None, f"false positive on prose: {turn!r} -> {got}"
    print("ok: dotted filenames and version numbers are not treated as URLs")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
        except Exception as e:
            failed += 1
            print(f"[FAIL] {t.__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    raise SystemExit(1 if failed else 0)
