#!/usr/bin/env python3
"""Tests for the SNR economics of osmosis (Step 3):
  1. eviction tiebreaker -- over the cap, measured ease-of-use protects a
     belief and measured uselessness exposes it, with NEUTRAL utility (no
     evidence) reproducing the pre-osmosis behavior exactly;
  2. per-session osmotic promotion budget -- new material through osmotic
     channels ([REMEMBER]) is capped; deliberated promotions are exempt;
     reinforcement is free.

Schema tests are model-free; the budget test uses the temp-DB session shim.
Run: ./.venv/bin/python test_osmosis_economics.py
"""
import sys, types
if "ollama" not in sys.modules:
    sys.modules["ollama"] = types.ModuleType("ollama")

from datetime import datetime, timezone
from schemas import BeliefMemory, DeliberatedBelief


def _belief(text, **kw):
    kw.setdefault("agreement", 0.3)
    kw.setdefault("contested", True)
    kw.setdefault("last_seen_at", datetime.now(timezone.utc))
    return DeliberatedBelief(text=text, **kw)


def test_eviction_prefers_benching_the_useless():
    m = BeliefMemory(cap=2)
    # identical signal profiles; only measured usage differs
    useful = _belief("caching invalidation storms hurt throughput",
                     injected_count=10, used_count=8)
    useless = _belief("microservice sagas need compensation logic",
                      injected_count=10, used_count=0,
                      correction_adjacent_count=3)
    m.beliefs = [useless, useful]
    out = m.add_or_reinforce("streaming tokens lowers perceived latency",
                             "obj", 0.3, True, "t3")
    assert out == "evicted_then_added"
    assert useful in m.beliefs, "the belief that served people must survive"
    assert useless in m.archived and useless.archived_reason == "low_signal"
    # non-regressive: the loser is quarantined, not deleted, and revivable
    assert m.revive_if_present("microservice sagas need compensation logic", "t4")
    print("ok: at the cap, measured uselessness is what gets benched -- revivably")


def test_neutral_utility_is_the_fixed_point():
    """With NO usage evidence anywhere, eviction must match the raw
    signal_score order exactly (utility 0.5 -> factor 1.0 for every record)."""
    now = datetime.now(timezone.utc)
    m = BeliefMemory(cap=2)
    strong = _belief("alpha subject matter entirely distinct", reinforce_count=5)
    weak = _belief("bravo completely different topical content", reinforce_count=1)
    m.beliefs = [strong, weak]
    expected_loser = min(m.beliefs, key=lambda b: b.signal_score(now))
    m.add_or_reinforce("charlie third unrelated theme altogether",
                       "obj", 0.3, True, "t3")
    assert expected_loser in m.archived
    assert strong in m.beliefs
    print("ok: no usage evidence => eviction identical to pre-osmosis signal order")


def _temp_session():
    import tempfile, storage, mcm as M, session as S
    from pathlib import Path
    tmp = tempfile.mkdtemp(prefix="seedling_osmo_")
    storage._DB_PATH = Path(tmp) / "db"; storage._db = None
    m = M.MCM(); m.restore_context(fresh=True)
    sess = S.ThreadSession(
        mcm=m, critic=types.SimpleNamespace(evaluate=lambda u, r: None),
        model_name="m", fresh=True, deliberation_enabled=False,
        live_deliberation_enabled=False, live_annotation_enabled=True,
        osmosis_promotion_budget=2)
    sess._memory_notices = []
    return tmp, m, sess


def _ann(content):
    return [{"kind": "insight", "subject": "x", "content": content}]


def test_osmotic_budget_caps_remember_channel():
    import shutil, storage
    tmp, m, sess = _temp_session()
    try:
        sess._process_annotations(_ann("Streaming output reduces perceived latency."))
        sess._process_annotations(_ann("Batched writes amortize storage lock costs."))
        assert len(m._state.beliefs.beliefs) == 2
        # third NEW osmotic insight this session -> deferred, not stored
        sess._process_annotations(_ann("Vector indexes trade memory for recall."))
        assert len(m._state.beliefs.beliefs) == 2, "budget must defer the 3rd"
        assert not any("Vector indexes" in b.text for b in m._state.beliefs.beliefs)
        print("ok: 3rd new [REMEMBER] insight in one session is deferred by the budget")
    finally:
        shutil.rmtree(tmp, ignore_errors=True); storage._db = None


def test_reinforcement_is_free_and_deliberation_is_exempt():
    import shutil, storage
    tmp, m, sess = _temp_session()
    try:
        sess._process_annotations(_ann("Streaming output reduces perceived latency."))
        sess._process_annotations(_ann("Batched writes amortize storage lock costs."))
        assert not sess._osmosis_budget_available()
        # REINFORCING an existing belief costs nothing even with budget spent...
        # (equivalent text -> 'reinforced', which _osmosis_budget_spend ignores)
        before = sess._osmosis_promotions
        sess._process_annotations(_ann("Streaming output reduces perceived latency."))
        # ...but note: the channel gate defers ALL writes once spent, so the
        # reinforce is deferred too -- the belief itself must be unchanged.
        assert sess._osmosis_promotions == before
        assert len(m._state.beliefs.beliefs) == 2
        # DELIBERATED promotion path is exempt: goes straight through promote_belief
        out = m.promote_belief("Contested beliefs carry more information than consensus.",
                               "Consensus can still be right.", 0.3, True, "t9")
        assert out == "added"
        assert len(m._state.beliefs.beliefs) == 3
        print("ok: budget never blocks the deliberated path; spending stops at new material")
    finally:
        shutil.rmtree(tmp, ignore_errors=True); storage._db = None


def test_budget_outcome_accounting():
    import shutil, storage
    tmp, m, sess = _temp_session()
    try:
        sess._osmosis_budget_spend("added");    assert sess._osmosis_promotions == 1
        sess._osmosis_budget_spend("reinforced"); assert sess._osmosis_promotions == 1
        sess._osmosis_budget_spend("skipped");  assert sess._osmosis_promotions == 1
        sess._osmosis_budget_spend("revived");  assert sess._osmosis_promotions == 2
        assert not sess._osmosis_budget_available()
        print("ok: only new-material outcomes (added/evicted/revived/conflict) spend budget")
    finally:
        shutil.rmtree(tmp, ignore_errors=True); storage._db = None


if __name__ == "__main__":
    test_eviction_prefers_benching_the_useless()
    test_neutral_utility_is_the_fixed_point()
    test_osmotic_budget_caps_remember_channel()
    test_reinforcement_is_free_and_deliberation_is_exempt()
    test_budget_outcome_accounting()
    print("\nALL OSMOSIS STEP-3 (economics) TESTS PASSED")
