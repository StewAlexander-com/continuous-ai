#!/usr/bin/env python3
"""Unit tests for the deliberation layer (no model needed \u2014 chat_fn is mocked).
Run: python3 test_deliberation.py
"""
import json
from pathlib import Path
import deliberation
from deliberation import deliberate, _agreement_from_objection


def test_agreement_mapping():
    # genuine consensus => high agreement, NOT contested (suspect/low-info)
    a, c = _agreement_from_objection("NO SUBSTANTIVE OBJECTION")
    assert a >= 0.9 and c is False
    a, c = _agreement_from_objection("")
    assert c is False
    # a real objection => lower agreement, contested
    a, c = _agreement_from_objection("This overgeneralizes; it fails when the user is offline.")
    assert a < 0.6 and c is True
    print("ok: agreement/contested mapping")


def test_contested_runs_synthesis():
    calls = []
    def mock_chat(model, messages):
        calls.append(messages[0]["content"][:20])
        # first call = antithesis (objection), second = synthesis
        if "Antithesis" in messages[0]["content"]:
            return "This is false when X; it ignores condition Y entirely."
        return "Revised: the claim holds only under condition Y."
    d = deliberate("All sessions improve coherence.", "t-contested", mock_chat, "m")
    assert d.contested is True
    assert "condition Y" in d.synthesis
    assert d.antithesis.startswith("This is false")
    assert len(calls) == 2, "contested path runs antithesis + synthesis"
    print("ok: contested -> objection preserved + synthesis incorporates it")


def test_uncontested_skips_synthesis():
    calls = []
    def mock_chat(model, messages):
        calls.append(1)
        return "NO SUBSTANTIVE OBJECTION"
    d = deliberate("The user is named Stew.", "t-consensus", mock_chat, "m")
    assert d.contested is False
    assert d.synthesis == "The user is named Stew."   # thesis kept
    assert d.agreement >= 0.9                          # consensus = suspect
    assert "low-information" in d.note
    assert len(calls) == 1, "uncontested path skips the synthesis call"
    print("ok: uncontested consensus flagged low-information, no synthesis call")


def test_failsafe_passthrough():
    def boom(model, messages):
        raise RuntimeError("model down")
    d = deliberate("Some insight.", "t-fail", boom, "m")
    assert d.synthesis == "Some insight."   # passthrough on error
    assert d.contested is False
    print("ok: model error -> safe passthrough (never breaks end())")


def test_ledger_appended(tmp_check=True):
    # point ledger at a temp dir to avoid polluting the repo
    orig = deliberation._LEDGER_DIR
    deliberation._LEDGER_DIR = Path("/tmp/_delib_test_ledger")
    try:
        def mock_chat(model, messages):
            return ("It depends on context."
                    if "Antithesis" in messages[0]["content"]
                    else "Revised: depends on context.")
        deliberate("x improves y.", "t-led", mock_chat, "m")
        p = deliberation._LEDGER_DIR / "ledger.jsonl"
        assert p.exists()
        rec = json.loads(p.read_text().strip().splitlines()[-1])
        assert rec["thread_id"] == "t-led" and "synthesis" in rec
        print("ok: deliberation appended to lineage ledger")
    finally:
        deliberation._LEDGER_DIR = orig


if __name__ == "__main__":
    test_agreement_mapping()
    test_contested_runs_synthesis()
    test_uncontested_skips_synthesis()
    test_failsafe_passthrough()
    test_ledger_appended()
    print("\nALL DELIBERATION TESTS PASSED")
