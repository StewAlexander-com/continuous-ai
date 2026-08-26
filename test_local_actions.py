#!/usr/bin/env python3
"""Locally-handled commands must be VISIBLE as having happened, while their
output stays out of the model. Run: ./.venv/bin/python test_local_actions.py
"""
from __future__ import annotations

import seedling
import session
from schemas import SecurityFinding

# A finding whose path and matched text must never reach the model.
SECRET_PATH = "/Users/someone/private-repo/deploy/.env"
SECRET_TEXT = "AWS_KEY=AKIAABCDEFGHIJKLMNOP"
FINDINGS = [
    SecurityFinding(path=SECRET_PATH, line=3, kind="aws_access_key", excerpt=SECRET_TEXT),
    SecurityFinding(path="/Users/someone/private-repo/net.md", line=9,
                    kind="ipv4_literal", excerpt="router 192.0.2.44"),
    SecurityFinding(path="/Users/someone/private-repo/net.md", line=11,
                    kind="ipv4_literal", excerpt="peer 192.0.2.45"),
]


def _sess():
    s = session.ThreadSession.__new__(session.ThreadSession)
    s._local_actions = []
    s.note_local_action = session.ThreadSession.note_local_action.__get__(s)
    s._activity_inject = session.ThreadSession._activity_inject.__get__(s)
    return s


def test_default_summary_leaks_nothing_at_all():
    """Default must keep 'nothing is sent to the model' literally true."""
    line = seedling._scan_summary_for_model(FINDINGS, "3 finding(s)", {})
    for forbidden in (SECRET_PATH, SECRET_TEXT, "192.0.2.44", "aws_access_key",
                      "ipv4_literal", ".env", "private-repo"):
        assert forbidden not in line, f"leaked {forbidden!r} in: {line!r}"
    assert "3" not in line, f"default must not carry counts: {line!r}"
    assert "not shared with you" in line, line
    # Not even the outcome: "no findings" vs "found something" is still a bit
    # about the user's disk.
    assert "no findings" not in line, line
    assert "were found" not in line, line
    print("ok: default scan summary carries no paths, text, kinds, counts or outcome")


def test_opt_in_summary_adds_counts_and_kinds_only():
    cfg = {"scan_summary_to_model": True}
    line = seedling._scan_summary_for_model(FINDINGS, "3 finding(s)", cfg)
    assert "3 finding(s)" in line, line
    assert "2 ipv4_literal" in line, line
    assert "1 aws_access_key" in line, line
    # Still never the sensitive parts.
    for forbidden in (SECRET_PATH, SECRET_TEXT, "192.0.2.44", ".env", "private-repo"):
        assert forbidden not in line, f"leaked {forbidden!r} in: {line!r}"
    print("ok: opt-in summary adds counts and kinds, never paths or matched text")


def test_empty_scan_summary():
    """An empty scan must be indistinguishable from a noisy one by default."""
    empty = seedling._scan_summary_for_model([], "no matching content found", {})
    noisy = seedling._scan_summary_for_model(FINDINGS, "3 finding(s)", {})
    assert empty == noisy, (
        "by default the summary must not disclose the outcome:\n"
        f"  empty: {empty!r}\n  noisy: {noisy!r}"
    )
    # With the knob on, an empty scan falls back to the neutral line rather
        # than inventing a zero breakdown.
    on = seedling._scan_summary_for_model([], "no matching content found",
                                          {"scan_summary_to_model": True})
    assert "not shared with you" in on, on
    print("ok: by default an empty scan is indistinguishable from a noisy one")


def test_activity_reaches_the_system_copy():
    s = _sess()
    s.note_local_action(seedling._scan_summary_for_model(FINDINGS, "3 finding(s)", {}))
    stored = {"role": "system", "content": "SYSTEM PROMPT"}
    out = s._activity_inject([dict(stored)])
    body = out[0]["content"]
    assert body.startswith("SYSTEM PROMPT"), body
    assert "cannot see it" in body, body
    assert "the above" in body, body
    # The instruction that prevents the observed failure: do NOT resolve such a
    # reference against the system prompt / persona / beliefs.
    assert "persona facts or beliefs" in body, body
    # Injection must be a copy.
    assert stored["content"] == "SYSTEM PROMPT"
    print("ok: local actions reach a COPY of the system message")


def test_no_actions_is_a_noop():
    s = _sess()
    out = s._activity_inject([{"role": "system", "content": "S"}])
    assert out[0]["content"] == "S", out
    print("ok: no local commands means no injected block")


def test_blank_notes_ignored_and_ledger_bounded():
    s = _sess()
    s.note_local_action("")
    s.note_local_action("   ")
    assert s._local_actions == [], s._local_actions
    for i in range(session._LOCAL_ACTIONS_MAX + 4):
        s.note_local_action(f"action {i}")
    assert len(s._local_actions) == session._LOCAL_ACTIONS_MAX
    body = s._activity_inject([{"role": "system", "content": "S"}])[0]["content"]
    assert "action 0" not in body, "oldest actions must age out"
    assert f"action {session._LOCAL_ACTIONS_MAX + 3}" in body, body
    print("ok: blank notes ignored, ledger bounded")


def test_scan_handler_accepts_a_session_and_notes_it():
    """The handler must take a session and record the run. Uses a stub so no
    filesystem scan happens."""
    class _Stub:
        def __init__(self):
            self.notes = []

        def note_local_action(self, s):
            self.notes.append(s)

    stub = _Stub()
    # usage path (no roots, gate off) must not crash with a session present
    seedling._handle_scan_command({"security_scan_enabled": False},
                                  ":scan --help", session=stub, ask=lambda p: False)
    print("ok: :scan handler accepts a session without regressing its usage path")


def _ref_sess(actions):
    s = session.ThreadSession.__new__(session.ThreadSession)
    s._local_actions = list(actions)
    s._handle_local_reference = session.ThreadSession._handle_local_reference.__get__(s)
    return s


SCAN_NOTE = (
    ":scan ran and printed its report in the user's terminal. The results, "
    "including whether anything was found, were not shared with you."
)


def test_the_reported_question_is_answered_honestly():
    """The exact turn from the bug report, typo included."""
    s = _ref_sess([SCAN_NOTE])
    out = s._handle_local_reference(
        "which of the above is false postives and what should I work on?"
    )
    assert out is not None, "must short-circuit"
    assert "can't see that" in out, out
    assert "never sent to me" in out, out
    assert "Paste" in out, out
    # It must not pretend to know anything about the contents.
    for bad in ("ipv4", "192.", "finding(s)", "false positive is"):
        assert bad not in out, out
    print("ok: the reported question gets an honest, actionable answer")


def test_variants_fire():
    s = _ref_sess([SCAN_NOTE])
    for q in (
        "which of those are false positives?",
        "what should I do about the findings?",
        "can you triage the results above?",
        "any of these worth fixing?",
        "explain the output",
        "are the hits real?",
    ):
        assert s._handle_local_reference(q) is not None, f"should fire: {q!r}"
    print("ok: common ways of pointing at unseen output all fire")


def test_never_fires_without_a_local_command():
    """On a fresh session this path must be inert."""
    s = _ref_sess([])
    for q in ("which of the above is false positives?",
              "what should I work on?",
              "explain the output"):
        assert s._handle_local_reference(q) is None, q
    print("ok: inert when no local command has run")


def test_ordinary_conversation_is_not_hijacked():
    s = _ref_sess([SCAN_NOTE])
    for q in (
        "what should I work on next?",
        "tell me about decorators",
        "how does the caution controller decide?",
        "remember that I live in Mebane",
        "what's above the fold on a landing page",   # 'above' without a target
        "",
    ):
        assert s._handle_local_reference(q) is None, f"must not fire: {q!r}"
    print("ok: ordinary turns still reach the model")


def test_pasted_block_reaches_the_model():
    """If the user brought the lines, the content IS in the turn — answer it."""
    s = _ref_sess([SCAN_NOTE])
    pasted = (
        "which of these are false positives?\n"
        "tests/test_net.py:152 [ipv4_literal] 192.168.1.1\n"
        "README.md:72 [ipv4_literal] 1.2.3.4\n"
    )
    assert s._handle_local_reference(pasted) is None, "pasted content must pass through"
    print("ok: a pasted block is not short-circuited")


def test_message_names_the_command_that_ran():
    s = _ref_sess([":enable security_scan_enabled \u2014 that capability is now on."])
    out = s._handle_local_reference("what did the output say?")
    assert out is not None and ":enable" in out, out
    print("ok: the reply names the command whose output is missing")


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
