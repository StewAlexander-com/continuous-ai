#!/usr/bin/env python3
"""Tests for the cross-thread DELIBERATED-BELIEF layer (L2b).

Covers the schema-level growth logic (no DB) AND the MCM/storage round-trip
(persist -> reload -> inject), proving beliefs actually accumulate from thread
to thread. Storage tests need lancedb; run with the venv:
  ./.venv/bin/python test_belief_growth.py
"""
import tempfile
from schemas import BeliefMemory, DeliberatedBelief, ContextState


def test_add_and_reinforce():
    m = BeliefMemory()
    assert m.add_or_reinforce("Coherence rises when objections are preserved.",
                              "May not hold offline.", 0.25, True, "t1") == "added"
    # near-identical re-derivation in a later thread -> reinforce, not duplicate
    out = m.add_or_reinforce("Coherence rises when objections are preserved across sessions.",
                             "Offline differs.", 0.30, True, "t2")
    assert out == "reinforced", out
    assert len(m.beliefs) == 1
    assert m.beliefs[0].reinforce_count == 2
    assert m.beliefs[0].last_seen_thread_id == "t2"
    print("ok: equivalent belief re-derived later reinforces (grows count, not list)")


def test_distinct_beliefs_not_merged():
    m = BeliefMemory()
    m.add_or_reinforce("Local models keep data on the user's machine.", "", 0.9, False, "t1")
    m.add_or_reinforce("Deliberation should preserve dissent, not average it.", "x", 0.3, True, "t2")
    assert len(m.beliefs) == 2, "distinct beliefs must NOT be silently merged"
    print("ok: lexically distinct beliefs stay separate (no over-merge)")


def test_more_contested_framing_wins_on_reinforce():
    m = BeliefMemory()
    m.add_or_reinforce("Caching improves throughput substantially.", "", 0.95, False, "t1")  # uncontested
    # equivalent re-derivation, this time contested (higher information)
    m.add_or_reinforce("Caching improves throughput substantially overall.",
                       "Fails under cache invalidation storms.", 0.25, True, "t2")
    assert len(m.beliefs) == 1, "equivalent re-derivation should merge"
    b = m.beliefs[0]
    assert b.reinforce_count == 2
    assert b.contested is True and b.agreement == 0.25, "adopts the higher-info framing"
    assert b.dissent == "Fails under cache invalidation storms."
    print("ok: reinforcement adopts the more-contested (higher-information) framing")


def test_cap_evicts_weakest_first():
    m = BeliefMemory(cap=3)
    # 3 DISTINCT contested beliefs, each reinforced once (strong, earned).
    distinct = [
        ("Preserving dissent beats averaging it away.",
         "Preserving dissent clearly beats averaging it away."),
        ("Local inference keeps user data on-device.",
         "Local inference reliably keeps user data on-device."),
        ("Adaptive depth prevents deliberation stalemates.",
         "Adaptive depth firmly prevents deliberation stalemates."),
    ]
    for first, again in distinct:
        assert m.add_or_reinforce(first, "a real objection here", 0.25, True, "t") == "added"
        assert m.add_or_reinforce(again, "a real objection here", 0.25, True, "t") == "reinforced"
    assert len(m.beliefs) == 3 and all(b.reinforce_count == 2 for b in m.beliefs)
    # now add a weak uncontested one -> over cap -> the weakest (it) is evicted
    m.add_or_reinforce("Some weak uncontested filler claim about widgets.", "", 0.95, False, "t")
    texts = [b.text for b in m.beliefs]
    assert len(m.beliefs) == 3
    assert not any("weak uncontested filler" in t.lower() for t in texts), \
        "weakest (uncontested) belief should be evicted first"
    print("ok: cap evicts the weakest belief, strong/contested ones survive")


def test_render_shows_dissent_and_count():
    m = BeliefMemory()
    m.add_or_reinforce("Preserving dissent raises coherence.", "Not under latency limits.", 0.25, True, "t1")
    m.add_or_reinforce("Preserving dissent raises coherence reliably.", "Latency.", 0.25, True, "t2")
    r = m.render()
    assert "Preserving dissent" in r
    assert "x2" in r, "reinforce count surfaced"
    assert "standing objection" in r, "dissent preserved in injection"
    print("ok: render surfaces earned beliefs with count + standing objection")


def test_storage_roundtrip_grows_across_threads():
    """The whole point: a promoted belief must PERSIST and be re-injected."""
    import os, storage, mcm as mcm_mod
    from pathlib import Path
    # isolate the DB so we don't touch real data
    tmp = tempfile.mkdtemp(prefix="seedling_belief_")
    storage._DB_PATH = Path(tmp) / "db"   # storage uses a Path
    storage._db = None                     # force re-connect at the new path
    try:
        m = mcm_mod.MCM()
        m.restore_context(fresh=False)  # session 1: empty
        # Thread A promotes a contested belief.
        o1 = m.promote_belief("Objection-preserving memory beats averaging.",
                              "Costs latency.", 0.25, True, "thread-A")
        assert o1 == "added", o1
        # Reload from storage (simulates a brand-new session/process).
        m2 = mcm_mod.MCM()
        injection = m2.restore_context(fresh=False)
        assert "Objection-preserving memory beats averaging" in injection, \
            "belief was NOT re-injected into the next session's context"
        assert "standing objection" in injection
        # Thread B re-derives an equivalent belief -> reinforces the SAME one.
        o2 = m2.promote_belief("Objection-preserving memory beats averaging away dissent.",
                               "Latency cost.", 0.30, True, "thread-B")
        assert o2 == "reinforced", o2
        # Reload again: count should have grown, still ONE belief.
        m3 = mcm_mod.MCM()
        m3.restore_context(fresh=False)
        beliefs = m3._state.beliefs.beliefs
        assert len(beliefs) == 1 and beliefs[0].reinforce_count == 2, \
            f"expected 1 belief x2, got {len(beliefs)} / {beliefs[0].reinforce_count if beliefs else 'none'}"
        print("ok: belief persists across reload AND grows when re-derived (cross-thread)")
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
        storage._db = None


if __name__ == "__main__":
    test_add_and_reinforce()
    test_distinct_beliefs_not_merged()
    test_more_contested_framing_wins_on_reinforce()
    test_cap_evicts_weakest_first()
    test_render_shows_dissent_and_count()
    test_storage_roundtrip_grows_across_threads()
    print("\nALL BELIEF-GROWTH TESTS PASSED")
