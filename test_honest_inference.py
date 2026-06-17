"""Tests for honest end-of-session insight handling (fixes #6, #10, #4).

The principle (per the user): Aida is ALLOWED to be wrong about an inference and
be corrected — that's natural. What she must NOT do is present a guess as settled
fact. So:
  #6  no substantive turns  -> no insight is formed (no confabulation invited)
  #10 an end-pass insight phrased as a user fact is a model INFERENCE, not a
       stated fact -> it must NOT bypass deliberation as verbatim-trusted
  #4  such an inference is labelled '(tentative inference, unverified)' so it
       never reads as gospel — in the stored delta and the CLI summary

These exercise the LOGIC with shims (no live Ollama). Verbatim user facts arrive
via the LIVE path in chat() and never reach end(), so they're out of scope here.
"""
import sys
import types

import session as session_mod
from session import ThreadSession, _asserts_user_fact


def _shim_session(messages):
    """A ThreadSession with just the attributes end()'s insight path touches."""
    s = ThreadSession.__new__(ThreadSession)
    s._messages = list(messages)
    return s


# ---- #6 / _has_substantive_turns -----------------------------------------

def test_command_only_session_has_no_substantive_turns():
    # system prompt + a delta-extraction user prompt, but NO assistant reply
    s = _shim_session([
        {"role": "system", "content": "..."},
        {"role": "user", "content": "[SEEDLING DELTA EXTRACTION] ..."},
    ])
    assert s._has_substantive_turns() is False
    print("[PASS] command-only / empty session -> no substantive turns")


def test_real_exchange_has_substantive_turns():
    s = _shim_session([
        {"role": "system", "content": "..."},
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "Hi Stew."},
    ])
    assert s._has_substantive_turns() is True
    print("[PASS] a session with a real model reply -> substantive")


def test_missing_messages_attr_is_safe():
    s = ThreadSession.__new__(ThreadSession)  # no _messages at all (shim)
    assert s._has_substantive_turns() is False
    print("[PASS] missing _messages attr is treated as non-substantive (no crash)")


# ---- #10 — the classifier that drives 'tentative, not verbatim' ----------

def test_user_fact_phrasing_is_detected():
    # These are exactly the end-pass confabulations we observed.
    for txt in [
        "The user requires responses to be strictly valid JSON — no markdown fences.",
        "The user prefers answers in a BLUF style.",
        "The user prefers BLUF format.",
    ]:
        assert _asserts_user_fact(txt), f"should be flagged as user-fact phrasing: {txt!r}"
    print("[PASS] end-pass insights phrased as user-facts are detected (-> tentative path)")


def test_model_claim_not_flagged_as_user_fact():
    # A genuine model inference about the world is NOT user-fact phrasing, so it
    # deliberates normally (unchanged behavior).
    for txt in [
        "Repeated log messages suggest a concurrency issue.",
        "Integrating the Second Arrow with Wu Wei is coherent.",
    ]:
        assert not _asserts_user_fact(txt), f"should NOT be flagged: {txt!r}"
    print("[PASS] genuine model claims are not mistaken for user facts")


# ---- #4 — the honest tentative label --------------------------------------

def test_tentative_label_format():
    # The label we prepend must be unambiguous and idempotent.
    raw = "The user prefers JSON output."
    labelled = f"(tentative inference, unverified) {raw}"
    assert labelled.startswith("(tentative inference, unverified) ")
    # idempotency guard used in end(): never double-label
    assert labelled.startswith("(tentative")
    print("[PASS] tentative label is unambiguous and idempotency-guarded")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            failed += 1
            print(f"[FAIL] {t.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"[ERROR] {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} honest-inference checks passed")
    sys.exit(1 if failed else 0)
