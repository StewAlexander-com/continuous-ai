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


def test_propose_feed_is_subset_of_verbs():
    extra = replcmds.PROPOSE_FEED - replcmds.VERBS
    assert not extra, extra
    assert "scan" not in replcmds.PROPOSE_FEED
    assert "scan" in replcmds.PROPOSE_NEVER
    print("[PASS] PROPOSE_FEED ⊆ VERBS and excludes :scan")


def test_parse_offer_whitelist_and_last_wins():
    assert replcmds.parse_offer("hello") is None
    assert replcmds.parse_offer("[offer :read ~/Desktop/x.md]") == ":read ~/Desktop/x.md"
    assert replcmds.parse_offer("Sure.\n[offer :search timeout in ~/proj]") == \
        ":search timeout in ~/proj"
    assert replcmds.parse_offer("[offer :more]") == ":more"
    assert replcmds.parse_offer("[offer :scan ~/secret]") is None
    assert replcmds.parse_offer("[offer :forget-doc x]") is None
    assert replcmds.parse_offer("[offer :read this file]") is None
    assert replcmds.parse_offer("[offer :read]") is None
    text = "[offer :read ~/a.md] talk [offer :read ~/b.md]"
    assert replcmds.parse_offer(text) == ":read ~/b.md"
    print("[PASS] parse_offer accepts feed verbs, drops :scan and English")


def test_strip_offers_and_confirm_lines():
    raw = "Here is a thought.\n[offer :read ~/x.md]\n"
    shown = replcmds.strip_offers(raw)
    assert "[offer" not in shown.lower()
    assert "Here is a thought" in shown
    assert replcmds.offer_reply_kind("ok") == "confirm"
    assert replcmds.offer_reply_kind("Y") == "confirm"
    assert replcmds.offer_reply_kind("n") == "decline"
    assert replcmds.offer_reply_kind("ok, later") is None
    assert replcmds.offer_reply_kind("please read it") is None
    assert replcmds.offer_reply_kind("sure") is None
    assert replcmds.offer_reply_kind("go ahead") is None
    print("[PASS] strip_offers + whole-line confirm/decline")


def test_offers_are_grounded_in_the_user_turn():
    assert replcmds.offer_fits_conversation(
        ":read ~/Desktop/x.md", "just read ~/Desktop/x.md")
    assert not replcmds.offer_fits_conversation(
        ":read ~/Desktop/x.md", "how was your day?")
    assert replcmds.offer_fits_conversation(
        ":more", "ok", has_attachment=True)
    assert not replcmds.offer_fits_conversation(
        ":more", "ok", has_attachment=False)
    assert replcmds.user_turn_may_warrant_offer("read ~/foo.py")
    assert not replcmds.user_turn_may_warrant_offer("what do you think?")
    print("[PASS] unsolicited offers are dropped; path/search turns may offer")


def test_offer_stream_filter_hides_tag():
    shown = []
    f = replcmds.OfferStreamFilter(lambda s: shown.append(s))
    f("I can read that. ")
    f("[off")
    f("er :read ~/x.md]\nMore prose.")
    f.flush()
    out = "".join(shown)
    assert "[offer" not in out.lower()
    assert "I can read that" in out
    assert "More prose" in out
    print("[PASS] OfferStreamFilter hides the tag while streaming")


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
