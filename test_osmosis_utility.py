#!/usr/bin/env python3
"""Tests for the usage-utility statistic (osmosis Step 1) on the model-owned
belief layer. "Signal is ease of use": these counters record whether an
injected belief actually served the exchange, deterministically and without
any model involvement. Measurement ONLY -- nothing here ranks, archives, or
deletes, and every field is additive so old records load unchanged.

Pure-schema tests; no model/DB needed.
Run: ./.venv/bin/python test_osmosis_utility.py
"""
from dataclasses import asdict
from datetime import datetime, timezone

from schemas import BeliefMemory, DeliberatedBelief


def test_neutral_prior_for_unobserved():
    b = DeliberatedBelief(text="Local-first memory reduces confabulation.")
    assert abs(b.usage_utility() - 0.5) < 1e-9, b.usage_utility()
    # a single injection with no use drifts BELOW neutral, gently
    b.injected_count = 1
    assert 0.3 < b.usage_utility() < 0.5
    # used every time it was injected -> above neutral, approaching 1
    b2 = DeliberatedBelief(text="x", injected_count=10, used_count=10)
    assert b2.usage_utility() > 0.9
    print("ok: unobserved belief scores a neutral 0.5; evidence moves it either way")


def test_utility_orders_and_correction_penalty():
    served = DeliberatedBelief(text="A", injected_count=10, used_count=8)
    ignored = DeliberatedBelief(text="B", injected_count=10, used_count=1)
    assert served.usage_utility() > ignored.usage_utility()
    # corrections erode usability -- same service record, worse utility
    bruised = DeliberatedBelief(text="C", injected_count=10, used_count=8,
                                correction_adjacent_count=4)
    assert served.usage_utility() > bruised.usage_utility()
    # bounded: utility always in [0, 1]
    extreme = DeliberatedBelief(text="D", injected_count=1, used_count=99,
                                correction_adjacent_count=0)
    assert 0.0 <= extreme.usage_utility() <= 1.0
    print("ok: utility grows with service, shrinks with corrections, stays in [0,1]")


def _mem_with(*texts_and_reinforce):
    m = BeliefMemory()
    for text, rc in texts_and_reinforce:
        m.beliefs.append(DeliberatedBelief(
            text=text, agreement=0.3, contested=True, reinforce_count=rc,
            last_seen_at=datetime.now(timezone.utc)))
    return m


def test_note_injected_bumps_only_rendered_set():
    m = _mem_with(("Strong belief about caching invalidation storms", 5),
                  ("Middling belief about retry backoff jitter", 2),
                  ("Weak belief about tab width preferences", 1))
    ids = m.note_injected(limit=2)
    assert len(ids) == 2
    by_id = {b.id: b for b in m.beliefs}
    injected = [by_id[i] for i in ids]
    left_out = [b for b in m.beliefs if b.id not in ids]
    assert all(b.injected_count == 1 for b in injected)
    assert all(b.injected_count == 0 for b in left_out)
    # the injected set is the SAME set render() shows (single ranking source)
    rendered = m.render(limit=2)
    assert all(b.text in rendered for b in injected)
    assert all(b.text not in rendered for b in left_out)
    print("ok: note_injected bumps exactly the beliefs render() injects")


def test_note_usage_lexical_attribution():
    m = _mem_with(("caching invalidation storms hurt throughput", 3),
                  ("retry backoff jitter smooths load spikes", 3))
    ids = m.note_injected(limit=2)
    reply = ("Careful: caching can backfire -- invalidation storms hurt "
             "throughput when entries churn.")
    used = m.note_usage(reply, ids)
    by_id = {b.id: b for b in m.beliefs}
    assert len(used) == 1
    assert "caching" in by_id[used[0]].text
    assert by_id[used[0]].used_count == 1
    other = [b for b in m.beliefs if b.id != used[0]][0]
    assert other.used_count == 0
    # a NON-injected belief never gets credit, even on perfect overlap
    outsider = DeliberatedBelief(text="caching invalidation storms hurt throughput")
    m.beliefs.append(outsider)
    m.note_usage(reply, ids)
    assert outsider.used_count == 0
    print("ok: usage credit goes only to injected beliefs whose content surfaced")


def test_correction_adjacent_bumps_only_injected():
    m = _mem_with(("alpha belief content", 2), ("beta belief content", 2))
    ids = m.note_injected(limit=1)
    n = m.note_correction_adjacent(ids)
    assert n == 1
    by_id = {b.id: b for b in m.beliefs}
    assert by_id[ids[0]].correction_adjacent_count == 1
    others = [b for b in m.beliefs if b.id not in ids]
    assert all(b.correction_adjacent_count == 0 for b in others)
    print("ok: correction adjacency lands only on the injected set")


def test_counters_roundtrip_and_old_records_load():
    b = DeliberatedBelief(text="roundtrip", injected_count=7, used_count=3,
                          correction_adjacent_count=1)
    d = asdict(b)
    for dtf in ("formed_at", "last_seen_at"):
        d[dtf] = d[dtf].isoformat() if hasattr(d[dtf], "isoformat") else d[dtf]
        d[dtf] = datetime.fromisoformat(d[dtf])  # what storage._belief() does
    b2 = DeliberatedBelief(**d)
    assert (b2.injected_count, b2.used_count, b2.correction_adjacent_count) == (7, 3, 1)
    assert abs(b2.usage_utility() - b.usage_utility()) < 1e-12
    # an OLD record (predates the counters) still loads, at neutral utility
    old = {k: v for k, v in d.items()
           if k not in ("injected_count", "used_count", "correction_adjacent_count")}
    b3 = DeliberatedBelief(**old)
    assert abs(b3.usage_utility() - 0.5) < 1e-9
    print("ok: counters survive the storage round-trip; old records load at neutral")


def test_measurement_never_mutates_membership():
    m = _mem_with(("one", 1), ("two", 1), ("three", 1))
    before = [b.id for b in m.beliefs]
    ids = m.note_injected(limit=3)
    m.note_usage("one two three and much more text here", ids)
    m.note_correction_adjacent(ids)
    assert [b.id for b in m.beliefs] == before
    assert not m.archived
    print("ok: Step 1 is measurement only -- no archive, no eviction, no reorder")


if __name__ == "__main__":
    test_neutral_prior_for_unobserved()
    test_utility_orders_and_correction_penalty()
    test_note_injected_bumps_only_rendered_set()
    test_note_usage_lexical_attribution()
    test_correction_adjacent_bumps_only_injected()
    test_counters_roundtrip_and_old_records_load()
    test_measurement_never_mutates_membership()
    print("\nALL OSMOSIS STEP-1 (usage utility) TESTS PASSED")
