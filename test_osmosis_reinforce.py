#!/usr/bin/env python3
"""Tests for osmotic reinforcement/decay (osmosis Step 2): one session's
measured usage evidence becomes tiny, capped, clamped salience nudges.
Deterministic; membership never changes here (archive/eviction stay with the
existing prune); a disabled flag means the counters still measure but nothing
is applied.

Pure-schema tests; no model/DB needed.
Run: ./.venv/bin/python test_osmosis_reinforce.py
"""
from datetime import datetime, timezone

from schemas import BeliefMemory, DeliberatedBelief


def _mem(*texts):
    m = BeliefMemory()
    for t in texts:
        m.beliefs.append(DeliberatedBelief(
            text=t, agreement=0.3, contested=True,
            last_seen_at=datetime.now(timezone.utc)))
    return m


def test_boost_requires_use_and_coherence():
    m = _mem("served belief", "idle belief")
    served, idle = m.beliefs
    inj = [served.id, idle.id]
    base_served, base_idle = served.effective_salience(), idle.effective_salience()
    # coherent session, served twice -> boost only the served belief
    rep = m.apply_osmosis({served.id: 2}, 0, avg_coherence=0.8, injected_ids=inj)
    assert len(rep) == 1 and rep[0][0] == served.id and rep[0][1] > 0
    assert served.effective_salience() > base_served
    assert idle.effective_salience() == base_idle
    # INCOHERENT session -> participation earns nothing
    before = served.effective_salience()
    rep = m.apply_osmosis({served.id: 3}, 0, avg_coherence=0.4, injected_ids=inj)
    assert rep == [] and served.effective_salience() == before
    print("ok: boost needs both real use and a coherent session")


def test_lifetime_boost_cap():
    m = _mem("polished belief")
    b = m.beliefs[0]
    for _ in range(100):
        m.apply_osmosis({b.id: 3}, 0, avg_coherence=0.9, injected_ids=[b.id])
    assert b.osmosis_boost_total <= 0.15 + 1e-9
    # once capped, further sessions apply nothing at all
    before = b.effective_salience()
    rep = m.apply_osmosis({b.id: 3}, 0, avg_coherence=0.9, injected_ids=[b.id])
    assert rep == [] and b.effective_salience() == before
    print("ok: lifetime osmotic gain is hard-capped -- use polishes, never crowns")


def test_decay_on_corrections_only_injected_and_clamped():
    m = _mem("injected belief", "benched belief")
    injected, benched = m.beliefs
    bi, bb = injected.effective_salience(), benched.effective_salience()
    # decay ignores the coherence gate (a correction is a correction)
    rep = m.apply_osmosis({}, 2, avg_coherence=0.9, injected_ids=[injected.id])
    assert len(rep) == 1 and rep[0][0] == injected.id and rep[0][1] < 0
    assert injected.effective_salience() < bi
    assert benched.effective_salience() == bb
    # clamped at 0 even under absurd correction counts
    for _ in range(100):
        m.apply_osmosis({}, 3, avg_coherence=0.9, injected_ids=[injected.id])
    assert injected.effective_salience() >= 0.0
    print("ok: correction decay hits only injected beliefs and clamps at 0")


def test_membership_is_never_mutated():
    m = _mem("one", "two", "three")
    ids = [b.id for b in m.beliefs]
    m.apply_osmosis({ids[0]: 3}, 3, avg_coherence=0.9, injected_ids=ids)
    assert [b.id for b in m.beliefs] == ids
    assert not m.archived
    print("ok: osmosis nudges salience only -- no archive, no eviction, no reorder")


def test_no_evidence_is_a_noop():
    m = _mem("untouched belief")
    b = m.beliefs[0]
    before = b.effective_salience()
    rep = m.apply_osmosis({}, 0, avg_coherence=0.9, injected_ids=[b.id])
    assert rep == [] and b.effective_salience() == before
    print("ok: a session with no usage evidence changes nothing")


def test_decayed_belief_remains_revivable_via_prune_path():
    """The regression that must never happen: osmotic decay silently destroying
    a belief. Decay can only lower salience; if signal then falls below the
    prune floor, the EXISTING prune quarantines (archive, not delete) and
    revive_if_present() still restores it."""
    m = _mem("fragile belief about retry backoff", "sturdy belief about caching")
    fragile = m.beliefs[0]
    fragile.agreement, fragile.contested = 0.95, False   # bland -> low info
    for _ in range(50):
        m.apply_osmosis({}, 3, avg_coherence=0.9, injected_ids=[fragile.id])
    m.prune_low_signal()
    if fragile in m.beliefs:
        # prune spared it (still above floor) -- also fine; nothing lost
        assert not m.archived
    else:
        assert fragile in m.archived and fragile.archived
        assert m.revive_if_present("fragile belief about retry backoff", "t9")
        assert fragile in m.beliefs and not fragile.archived
    print("ok: worst-case decay ends in revivable quarantine, never deletion")


if __name__ == "__main__":
    test_boost_requires_use_and_coherence()
    test_lifetime_boost_cap()
    test_decay_on_corrections_only_injected_and_clamped()
    test_membership_is_never_mutated()
    test_no_evidence_is_a_noop()
    test_decayed_belief_remains_revivable_via_prune_path()
    print("\nALL OSMOSIS STEP-2 (reinforcement/decay) TESTS PASSED")
