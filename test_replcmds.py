#!/usr/bin/env python3
"""Colon-command registry + typo intercept. No live model.

Run: ./.venv/bin/python test_replcmds.py
"""
from __future__ import annotations

import inputsafe as I
import replcmds


def test_verbs_cover_dispatched_commands():
    """If-chain verbs must live in VERBS or typos of them leak to chat."""
    required = {
        "help", "?", "status", "setup", "dispositions", "learning",
        "model", "models", "read", "more", "search", "scan", "allow",
        "capabilities", "caps", "enable", "disable", "reflect",
        "forget-doc", "voice", "quiet", "theme", "tune", "q",
    }
    missing = required - replcmds.VERBS
    extra = replcmds.VERBS - required
    assert not missing, f"VERBS missing {missing}"
    assert not extra, f"VERBS extra {extra} — update this freeze if the command is real"
    print("[PASS] VERBS freeze matches the dispatched set")


def test_colon_verb_shapes():
    assert replcmds.colon_verb(":theme dark") == "theme"
    assert replcmds.colon_verb(":theme:dark") == "theme"
    assert replcmds.colon_verb(":forget-doc x") == "forget-doc"
    assert replcmds.colon_verb(":?") == "?"
    assert replcmds.colon_verb(":)") is None
    assert replcmds.colon_verb(":D") is None
    assert replcmds.colon_verb(":P") is None
    assert replcmds.colon_verb("hello") is None
    assert replcmds.colon_verb(":") == ""
    print("[PASS] colon_verb parses command shape, ignores smileys")


def test_known_verb_leftover_is_not_chat():
    """If the if-chain misses a known verb, still do not send it to the model."""
    note = replcmds.colon_fallthrough_notice(":help")
    assert note is not None and "not sent as chat" in note.lower()
    note = replcmds.colon_fallthrough_notice(":voice potato")
    assert note is not None and "not sent as chat" in note.lower()
    print("[PASS] known-verb leftovers are not sent as chat")


def test_typos_did_you_mean_and_keep_args():
    n = replcmds.colon_fallthrough_notice(":them dark")
    assert n is not None and "did you mean :theme dark" in n, n
    assert "Not sent as chat" in n
    n = replcmds.colon_fallthrough_notice(":hel")
    assert n is not None and "did you mean :help" in n, n
    n = replcmds.colon_fallthrough_notice(":serach foo")
    assert n is not None and "did you mean :search foo" in n, n
    n = replcmds.colon_fallthrough_notice(":readme")
    assert n is not None and "did you mean :read" in n, n
    n = replcmds.colon_fallthrough_notice(":themes")
    assert n is not None and "did you mean :theme" in n, n
    print("[PASS] typos suggest the verb and keep args")


def test_far_typos_point_at_help_not_a_guess():
    n = replcmds.colon_fallthrough_notice(":xyzzy")
    assert n is not None
    assert "did you mean" not in n
    assert ":help" in n
    assert "Not sent as chat" in n
    print("[PASS] unknown verbs without a close match are not guessed")


def test_smileys_are_chat_not_commands():
    assert replcmds.colon_fallthrough_notice(":)") is None
    assert replcmds.colon_fallthrough_notice(":( ok") is None
    assert replcmds.colon_fallthrough_notice(":D") is None
    print("[PASS] smileys are not intercepted")


def test_looks_like_command_uses_the_registry():
    assert I.looks_like_command(":search foo")
    assert I.looks_like_command(":scan")
    assert I.looks_like_command(":allow ~/x")
    assert I.looks_like_command(":enable scan")
    assert I.looks_like_command(":quiet")
    assert I.looks_like_command(":caps")
    assert I.looks_like_command(":theme dark")
    assert I.looks_like_command(":q")
    assert not I.looks_like_command(":themes")
    assert not I.looks_like_command(":readme")
    assert not I.looks_like_command("help me")
    print("[PASS] looks_like_command tracks VERBS")


def test_voice_unknown_form_is_not_chat():
    n = replcmds.colon_fallthrough_notice(":voice potato")
    assert n is not None and "not sent as chat" in n.lower()
    print("[PASS] known verb + junk form is not sent as chat")


def test_missing_colon_offer_command_shaped():
    assert replcmds.missing_colon_offer("theme dark") == ":theme dark"
    assert replcmds.missing_colon_offer("theme:dark") == ":theme dark"
    assert replcmds.missing_colon_offer("them dark") == ":theme dark"
    assert replcmds.missing_colon_offer("help") == ":help"
    assert replcmds.missing_colon_offer("status") == ":status"
    assert replcmds.missing_colon_offer("search needle") == ":search needle"
    assert replcmds.missing_colon_offer("read ~/x.py") == ":read ~/x.py"
    assert replcmds.missing_colon_offer("voice off") == ":voice off"
    assert replcmds.missing_colon_offer("enable scan") == ":enable scan"
    print("[PASS] missing ':' reconstructs the command")


def test_missing_colon_skips_english_and_colon_lines():
    assert replcmds.missing_colon_offer("help me understand") is None
    assert replcmds.missing_colon_offer("theme of the paper") is None
    assert replcmds.missing_colon_offer("search for meaning") is None
    assert replcmds.missing_colon_offer("read this file") is None
    assert replcmds.missing_colon_offer("status of the build") is None
    assert replcmds.missing_colon_offer(":theme dark") is None
    assert replcmds.missing_colon_offer("hello there") is None
    assert replcmds.missing_colon_offer("q") is None
    print("[PASS] English and real colon lines are not offered")


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
