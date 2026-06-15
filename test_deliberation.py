#!/usr/bin/env python3
"""Unit tests for the deliberation layer (no model needed \u2014 chat_fn is mocked).
Run: python3 test_deliberation.py
"""
import json
from pathlib import Path
import deliberation
from deliberation import (
    deliberate, MAX_ROUNDS,
    _objection_strength, _agreement_from_strength, _rounds_for,
)


def test_strength_classification():
    assert _objection_strength("NO SUBSTANTIVE OBJECTION") == "none"
    assert _objection_strength("") == "none"
    assert _objection_strength("x") == "none"          # too short to be real
    assert _objection_strength("This is a minor nitpick, mostly fine.") == "weak"
    assert _objection_strength(
        "This is false; it ignores condition Y and is unsupported.") == "strong"
    # no hedge, no hard-contradiction word => moderate
    assert _objection_strength(
        "It depends heavily on the deployment environment.") == "moderate"
    print("ok: objection-strength classification")


def test_agreement_mapping():
    # genuine consensus => high agreement, NOT contested (suspect/low-info)
    a, c = _agreement_from_strength("none")
    assert a >= 0.9 and c is False
    # a real objection => lower agreement, contested; strength orders agreement
    aw, cw = _agreement_from_strength("weak")
    am, cm = _agreement_from_strength("moderate")
    asg, csg = _agreement_from_strength("strong")
    assert cw is True and cm is True and csg is True
    assert aw > am > asg, "stronger objection => lower agreement"
    print("ok: strength -> agreement/contested mapping")


def test_rounds_for_capped():
    assert _rounds_for("none") == 0
    assert _rounds_for("weak") == 1
    assert _rounds_for("moderate") == 1
    assert 1 <= _rounds_for("strong") <= MAX_ROUNDS
    print("ok: rounds scale with strength and stay under the hard cap")


def test_contested_runs_synthesis():
    # A weak objection earns exactly one synthesis round: antithesis + synthesis.
    calls = []
    def mock_chat(model, messages):
        calls.append(messages[0]["content"][:20])
        if "Antithesis" in messages[0]["content"]:
            return "Minor nitpick: it mostly holds but condition Y matters."
        return "Revised: the claim holds only under condition Y."
    d = deliberate("All sessions improve coherence.", "t-contested", mock_chat, "m")
    assert d.contested is True
    assert "condition Y" in d.synthesis
    assert d.antithesis.startswith("Minor nitpick")
    assert len(calls) == 2, "weak path runs antithesis + 1 synthesis"
    assert d.extra.get("rounds") == 1
    print("ok: contested -> objection preserved + synthesis incorporates it")


def test_strong_objection_escalates_then_caps():
    # A strong objection earns up to 2 rounds. We re-challenge between synthesis
    # rounds; here every objection stays strong, so we hit the budget and stop.
    calls = {"anti": 0, "syn": 0}
    def mock_chat(model, messages):
        if "Antithesis" in messages[0]["content"]:
            calls["anti"] += 1
            return "This is false and unsupported; it contradicts the offline case."
        calls["syn"] += 1
        return f"Revised v{calls['syn']}: narrowed to the online case only."
    d = deliberate("X always holds.", "t-strong", mock_chat, "m")
    assert d.contested is True
    assert d.extra["strength"] == "strong"
    assert d.extra["rounds"] == _rounds_for("strong"), "used full strong budget"
    assert d.extra["rounds"] <= MAX_ROUNDS
    # 2 synthesis rounds + (initial antithesis + 1 re-challenge) = 4 calls total
    assert calls["syn"] == _rounds_for("strong")
    print("ok: strong objection escalates rounds but stays under the cap")


def test_convergence_early_exit():
    # Strong first objection (budget 2), but the FIRST synthesis survives the
    # re-challenge (NO SUBSTANTIVE OBJECTION) -> we stop early at 1 round.
    state = {"anti": 0}
    def mock_chat(model, messages):
        if "Antithesis" in messages[0]["content"]:
            state["anti"] += 1
            if state["anti"] == 1:
                return "This is false and unsupported in the offline case."
            return "NO SUBSTANTIVE OBJECTION"   # synthesis now survives
        return "Revised: scoped to the online case."
    d = deliberate("X always holds.", "t-converge", mock_chat, "m")
    assert d.extra["strength"] == "strong"
    assert d.extra["rounds"] == 1, "converged after one round, did not use full budget"
    assert d.contested is False, "final re-challenge found no objection -> consensus"
    print("ok: convergence on re-challenge exits before the budget is spent")


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
    test_strength_classification()
    test_agreement_mapping()
    test_rounds_for_capped()
    test_contested_runs_synthesis()
    test_strong_objection_escalates_then_caps()
    test_convergence_early_exit()
    test_uncontested_skips_synthesis()
    test_failsafe_passthrough()
    test_ledger_appended()
    print("\nALL DELIBERATION TESTS PASSED")
