#!/usr/bin/env python3
"""Propose → confirm → runtime. No live model.

Run: ./.venv/bin/python test_command_offers.py
"""
from __future__ import annotations

import types

import replcmds
import seedling
import session as S


def test_pending_confirm_runs_only_on_ok():
    s = types.SimpleNamespace(_pending_offer=":read ~/x.md")
    ran = []
    orig = seedling._run_confirmed_offer
    seedling._run_confirmed_offer = lambda cmd, **k: ran.append(cmd)
    kw = dict(session=s, config={}, read_state={}, read_pick_state={})
    try:
        assert seedling._try_pending_offer_turn("tell me more", **kw) is False
        assert s._pending_offer is None and ran == []
        s._pending_offer = ":read ~/x.md"
        assert seedling._try_pending_offer_turn("ok", **kw) is True
        assert ran == [":read ~/x.md"]
        assert s._pending_offer is None
        s._pending_offer = ":search foo"
        ran.clear()
        assert seedling._try_pending_offer_turn("n", **kw) is True
        assert ran == [] and s._pending_offer is None
        print("[PASS] confirm required; chat expires pending; decline is quiet")
    finally:
        seedling._run_confirmed_offer = orig


def test_note_offer_sets_pending_from_reply():
    s = types.SimpleNamespace()
    seedling._note_offer_from_reply(
        s, "Sure.\n[offer :read ~/notes.md]",
        last_user="please read ~/notes.md")
    assert s._pending_offer == ":read ~/notes.md"
    seedling._note_offer_from_reply(s, "No path named.", last_user="hello")
    assert s._pending_offer is None
    seedling._note_offer_from_reply(
        s, "[offer :read ~/notes.md]", last_user="how are you?")
    assert s._pending_offer is None
    seedling._note_offer_from_reply(s, "[offer :scan ~/x]", last_user="read ~/x")
    assert s._pending_offer is None
    print("[PASS] note_offer stores grounded whitelist offers only")


def test_catalog_inject_does_not_mutate_stored_prompt():
    sess = S.ThreadSession.__new__(S.ThreadSession)
    sess._pending_offer = ":read ~/a.md"
    sess._messages = [{"role": "user", "content": "read ~/a.md"}]
    stored = [{"role": "system", "content": "SYS"}]
    out = sess._command_offer_inject(stored)
    assert stored[0]["content"] == "SYS"
    body = out[0]["content"]
    assert body.startswith("SYS")
    assert "Stay in conversation" in body
    assert "[offer :read" in body
    assert "Never [offer] :scan" in body
    assert ":read ~/a.md" in body
    # Ordinary chat: no catalog lecture
    sess._pending_offer = None
    sess._messages = [{"role": "user", "content": "what do you think?"}]
    quiet = sess._command_offer_inject(stored)
    assert quiet[0]["content"] == "SYS"
    print("[PASS] catalog inject is a copy; only on path/search turns; :scan forbidden")


def test_help_mentions_offers():
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        seedling._handle_help_command()
    out = buf.getvalue()
    assert "quietly offer" in out.lower()
    assert "y/ok" in out.lower()
    print("[PASS] :help names a quiet offer loop")


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
