#!/usr/bin/env python3
"""Tests for the DOUBT-SCOPE GUARD: deliberation may challenge the model's own
reasoning, but must NEVER manufacture doubt about a user-stated fact (the user is
the authority on those). Real doubt about model inferences is preserved.

Mocks the model + DB; run with system python3 (no Ollama/lancedb needed for the
pure gate tests) or the venv for the resemblance test.
"""
import sys, types
if "ollama" not in sys.modules:
    sys.modules["ollama"] = types.ModuleType("ollama")

import session as S


def test_pattern_detects_user_facts():
    yes = [
        "The user's name is Stew and he lives in Mebane.",
        "The user lives in Mebane, North Carolina.",
        "The user named me 'Aida'.",
        "my name is Stew",
        "I live in North Carolina",
        "The user wants me to lead with BLUF.",
        "The user prefers a BLUF format.",
        "your name is Aida",
    ]
    for t in yes:
        assert S._asserts_user_fact(t), f"should be a user fact: {t!r}"
    print("ok: user-fact assertions are detected")


def test_pattern_allows_model_inferences():
    # genuine model insights -> NOT user facts -> still deliberated (real doubt ok)
    no = [
        "Local-first memory appears to reduce confabulation under load.",
        "Preserving dissent beats averaging it away when forming beliefs.",
        "Adaptive deliberation depth prevents stalemates.",
        "Caching improves throughput substantially.",
        "No insight extracted.",
    ]
    for t in no:
        assert not S._asserts_user_fact(t), f"should NOT be flagged as user fact: {t!r}"
    print("ok: genuine model inferences are NOT flagged (real doubt preserved)")


def test_resemblance_to_stored_persona_fact():
    # needs the real MCM/storage; isolate a temp DB
    import tempfile, shutil, storage, mcm as M
    from pathlib import Path
    tmp = tempfile.mkdtemp(prefix="seedling_doubt_")
    storage._DB_PATH = Path(tmp) / "db"; storage._db = None
    try:
        m = M.MCM(); m.restore_context(fresh=True)
        m.promote_persona_fact("my name is Stew and I live in Mebane", "identity", "t1")
        # An insight that re-states that fact (even reworded) should resemble it.
        assert m.resembles_persona_fact("The user is named Stew, located in Mebane.")
        # An unrelated model insight should NOT resemble it.
        assert not m.resembles_persona_fact(
            "Local-first memory reduces confabulation under load.")
        print("ok: insight resembling a stored persona fact is recognized as user-anchored")
    finally:
        shutil.rmtree(tmp, ignore_errors=True); storage._db = None


def test_live_gate_drops_user_fact_candidate():
    # The per-turn live gate must drop a response that merely restates a user fact.
    shim = type("S", (), {})()
    # give the shim a resembles_persona_fact via a fake mcm
    shim.mcm = types.SimpleNamespace(resembles_persona_fact=lambda t: False)
    g = S.ThreadSession._live_deliberation_candidate
    # user-fact echo (long enough to pass length gate) -> dropped by pattern
    echoed = ("Understood, I will remember that your name is Stew and that you "
              "live in Mebane going forward in our conversations together.")
    assert g(shim, echoed) is None, "user-fact echo must not be deliberated"
    # a genuine substantive model claim -> passes the gate
    claim = ("Local-first memory appears to reduce confabulation because the "
             "model cannot fetch unverifiable external context.")
    assert g(shim, claim) is not None, "genuine model claim should be deliberated"
    print("ok: live gate drops user-fact echoes but keeps genuine model claims")


if __name__ == "__main__":
    test_pattern_detects_user_facts()
    test_pattern_allows_model_inferences()
    test_resemblance_to_stored_persona_fact()
    test_live_gate_drops_user_fact_candidate()
    print("\nALL DOUBT-SCOPE TESTS PASSED")
