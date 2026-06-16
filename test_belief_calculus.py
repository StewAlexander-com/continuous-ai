#!/usr/bin/env python3
"""Tests for the autonomous SNR calculus + conflict resolution + quarantine on
the model-owned belief layer. Deterministic; archive-not-delete; nothing here
touches persona/user facts.

Pure-schema tests need no model/DB. The storage round-trip needs lancedb (venv).
Run: ./.venv/bin/python test_belief_calculus.py
"""
from datetime import datetime, timezone, timedelta
from schemas import BeliefMemory, DeliberatedBelief


def test_signal_score_orders_by_real_signal():
    now = datetime.now(timezone.utc)
    # strongly contested + re-earned + fresh  vs  bland + stale
    strong = DeliberatedBelief(text="A", agreement=0.2, contested=True,
                               reinforce_count=5, last_seen_at=now)
    weak = DeliberatedBelief(text="B", agreement=0.95, contested=False,
                             reinforce_count=1,
                             last_seen_at=now - timedelta(days=120))
    assert strong.signal_score(now) > weak.signal_score(now)
    # recency matters: same belief decays as it goes untouched
    fresh = DeliberatedBelief(text="C", agreement=0.3, contested=True,
                              reinforce_count=2, last_seen_at=now)
    aged = DeliberatedBelief(text="C", agreement=0.3, contested=True,
                             reinforce_count=2,
                             last_seen_at=now - timedelta(days=90))
    assert fresh.signal_score(now) > aged.signal_score(now)
    # conflict losses erode signal
    bruised = DeliberatedBelief(text="D", agreement=0.3, contested=True,
                                reinforce_count=2, challenged_count=4,
                                last_seen_at=now)
    clean = DeliberatedBelief(text="D", agreement=0.3, contested=True,
                              reinforce_count=2, last_seen_at=now)
    assert clean.signal_score(now) > bruised.signal_score(now)
    print("ok: signal_score grows with re-earning/info, decays with age/losses")


def test_conflict_detected_not_silently_merged():
    m = BeliefMemory()
    assert m.add_or_reinforce("Local-first memory reduces confabulation.",
                              "Adds latency.", 0.3, True, "t1") == "added"
    # opposite polarity, same subject -> must be a CONFLICT, not a reinforce
    out = m.add_or_reinforce("Local-first memory does not reduce confabulation.",
                             "x", 0.4, True, "t2")
    assert out == "conflict", out
    assert m._last_conflict_index == 0
    # both are present until the caller resolves (no silent loss)
    assert len(m.beliefs) == 2
    print("ok: a contradicting belief is flagged 'conflict', never merged as agreement")


def test_resolve_conflict_archives_loser_keeps_winner():
    m = BeliefMemory()
    m.add_or_reinforce("Caching always improves throughput.", "", 0.9, False, "t1")
    assert m.add_or_reinforce("Caching does not always improve throughput.",
                              "Invalidation storms.", 0.3, True, "t2") == "conflict"
    # deliberation picks the nuanced (new) belief as winner
    winner = "Caching does not always improve throughput; invalidation can hurt."
    out = m.resolve_conflict(winner, "Invalidation storms.", 0.3, True, "t2")
    assert out == "conflict_resolved", out
    active = [b.text for b in m.beliefs]
    archived = [b.text for b in m.archived]
    assert len(m.beliefs) == 1 and winner.split(";")[0][:10] in active[0]
    assert len(m.archived) == 1
    assert m.archived[0].archived and m.archived[0].archived_reason.startswith("lost_conflict")
    print("ok: conflict resolution keeps the winner active, archives the loser (retained)")


def test_low_signal_is_quarantined_not_deleted():
    now = datetime.now(timezone.utc)
    m = BeliefMemory(prune_floor=0.5)
    m.add_or_reinforce("A strong contested belief about coherence.", "obj", 0.2, True, "t1")
    m.add_or_reinforce("A bland uncontested aside about widgets.", "", 0.97, False, "t2")
    # age the bland one so its signal falls below the floor
    for b in m.beliefs:
        if "widgets" in b.text:
            b.last_seen_at = now - timedelta(days=200)
    moved = m.prune_low_signal(now)
    assert any("widgets" in b.text for b in moved), "low-signal belief should be quarantined"
    assert all("widgets" not in b.text for b in m.beliefs), "removed from active"
    assert any("widgets" in b.text for b in m.archived), "but RETAINED in archive (not deleted)"
    assert m.archived[0].archived_reason == "low_signal"
    print("ok: low-signal beliefs are quarantined (retained + auditable), not deleted")


def test_quarantined_belief_revives_when_reearned():
    now = datetime.now(timezone.utc)
    m = BeliefMemory(prune_floor=0.5)
    # keep one strong belief active (prune never empties the active list), and a
    # second, low-signal one that should be quarantined then revived.
    m.add_or_reinforce("A strong contested anchor belief about coherence.", "obj", 0.2, True, "t0")
    m.add_or_reinforce("Edge inference keeps latency low under load.", "", 0.95, False, "t1")
    for b in m.beliefs:
        if "Edge inference" in b.text:
            b.last_seen_at = now - timedelta(days=300)
    m.prune_low_signal(now)
    assert any("Edge inference" in b.text for b in m.archived) and \
        all("Edge inference" not in b.text for b in m.beliefs)
    # re-earning it in a later thread REVIVES it from quarantine
    out = m.add_or_reinforce("Edge inference keeps latency low under load.",
                             "", 0.9, False, "t9")
    assert out == "revived", out
    assert any("Edge inference" in b.text for b in m.beliefs), "revived back to active"
    assert all("Edge inference" not in b.text for b in m.archived), "removed from archive"
    revived = next(b for b in m.beliefs if "Edge inference" in b.text)
    assert revived.reinforce_count >= 2
    print("ok: a re-earned quarantined belief revives (nothing is permanently lost)")


def test_cap_eviction_archives_lowest_signal():
    now = datetime.now(timezone.utc)
    m = BeliefMemory(cap=2)
    m.add_or_reinforce("Belief alpha about coherence preservation.", "obj", 0.2, True, "t1")
    m.add_or_reinforce("Belief beta about throughput characteristics.", "obj", 0.2, True, "t2")
    # third distinct belief, very low signal -> over cap -> it gets archived
    m.add_or_reinforce("Belief gamma a bland uncontested filler note.", "", 0.98, False, "t3")
    assert len(m.beliefs) == 2
    assert len(m.archived) == 1, "cap eviction must archive, not delete"
    print("ok: cap eviction archives the lowest-signal belief instead of deleting")


def test_storage_roundtrip_preserves_archive_and_fields():
    import tempfile, shutil, storage, mcm as M
    from pathlib import Path
    tmp = tempfile.mkdtemp(prefix="seedling_calc_")
    storage._DB_PATH = Path(tmp) / "db"; storage._db = None
    try:
        m = M.MCM(); m.restore_context(fresh=True)
        m.promote_belief("Deliberation preserves dissent across threads.",
                         "Costs latency.", 0.25, True, "t1")
        m.promote_belief("A bland uncontested filler note about widgets.",
                         "", 0.97, False, "t2")
        # age the bland one so a prune quarantines it (keep the strong one active)
        for b in m._state.beliefs.beliefs:
            if "widgets" in b.text:
                b.last_seen_at = datetime.now(timezone.utc) - timedelta(days=400)
        m._state.beliefs.prune_floor = 0.9
        m.prune_beliefs()
        # reload in a fresh MCM and confirm the archive + fields survived
        m2 = M.MCM(); m2.restore_context(fresh=False)
        b2 = m2._state.beliefs
        assert len(b2.archived) == 1, "archived tier must persist across reload"
        assert isinstance(b2.archived[0].last_seen_at, datetime), "datetime field round-trips"
        assert b2.archived[0].archived is True
        print("ok: archive tier + SNR fields persist across a storage reload")
    finally:
        shutil.rmtree(tmp, ignore_errors=True); storage._db = None


if __name__ == "__main__":
    test_signal_score_orders_by_real_signal()
    test_conflict_detected_not_silently_merged()
    test_resolve_conflict_archives_loser_keeps_winner()
    test_low_signal_is_quarantined_not_deleted()
    test_quarantined_belief_revives_when_reearned()
    test_cap_eviction_archives_lowest_signal()
    test_storage_roundtrip_preserves_archive_and_fields()
    print("\nALL BELIEF-CALCULUS TESTS PASSED")
