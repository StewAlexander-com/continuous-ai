#!/usr/bin/env python3
"""Tests for the sleep pass (osmosis Step 4). The analysis functions are pure
and model-free; orchestration is tested with an INJECTED fake deliberation
(house pattern) and a temp-DB session shim. Everything asserts the safety
posture: capped spend, budget respected, archive-not-delete, external evidence
required for parole.

Run: ./.venv/bin/python test_reflection.py
"""
import sys, types
if "ollama" not in sys.modules:
    sys.modules["ollama"] = types.ModuleType("ollama")

from datetime import datetime, timezone

from schemas import ContextState, DeliberatedBelief, ThreadDelta
import reflection as R


def _belief(text, **kw):
    kw.setdefault("agreement", 0.3)
    kw.setdefault("contested", True)
    kw.setdefault("last_seen_at", datetime.now(timezone.utc))
    return DeliberatedBelief(text=text, **kw)


def _delta(insight, coherence, thread_id, **kw):
    return ThreadDelta(thread_id=thread_id, insight_gained=insight,
                       coherence_score=coherence, **kw)


# ---------- deterministic analysis ----------

def test_parole_needs_recurrence_and_clean_record():
    st = ContextState()
    recurring = _belief("retry backoff jitter smooths load spikes")
    recurring.archived, recurring.archived_reason = True, "low_signal"
    unrelated = _belief("tab width preferences vary wildly")
    unrelated.archived, unrelated.archived_reason = True, "low_signal"
    loser = _belief("adding jitter to retry backoff smooths load spikes today")
    loser.archived, loser.archived_reason = True, "lost_conflict:something"
    denied = _belief("jitter on retry backoff smooths load spikes broadly")
    denied.archived, denied.archived_reason = True, "low_signal;parole_denied"
    st.beliefs.archived = [recurring, unrelated, loser, denied]
    # recent GATED experience mentions the subject again
    st.thread_deltas = [
        _delta("adding jitter to retry backoff smooths load spikes under contention",
               0.8, "t1"),
    ]
    cands = R.parole_candidates(st)
    assert cands == [recurring], [b.text for b in cands]
    # no gated experience at all -> nobody gets a hearing
    st.thread_deltas = [_delta("anything", 0.3, "t2")]   # sub-gate: not evidence
    assert R.parole_candidates(st) == []
    print("ok: parole needs external recurrence; conflict-losers and denied stay out")


def test_mining_requires_convergence_across_threads():
    st = ContextState()
    insight = "streaming tokens to the client lowers perceived latency"
    st.thread_deltas = [
        _delta(insight, 0.45, "t1"),
        _delta(insight + " noticeably", 0.4, "t2"),
        _delta(insight, 0.35, "t3"),
        _delta("a completely different unrelated observation", 0.45, "t4"),
        _delta("another lonely thought about gardening habits", 0.4, "t5"),
    ]
    cands = R.mine_delta_clusters(st)
    assert len(cands) == 1 and "perceived latency" in cands[0]
    # the representative carries the HIGHEST-coherence framing
    assert cands[0] == insight
    # two threads is not convergence
    st2 = ContextState()
    st2.thread_deltas = [_delta(insight, 0.45, "t1"), _delta(insight, 0.4, "t2")]
    assert R.mine_delta_clusters(st2) == []
    # same-thread repetition is not convergence either
    st3 = ContextState()
    st3.thread_deltas = [_delta(insight, 0.45, "t1")] * 4
    assert R.mine_delta_clusters(st3) == []
    print("ok: mining admits only >=3-thread convergence, best framing wins")


def test_mining_excludes_gated_quarantined_emergent_and_known():
    st = ContextState()
    insight = "background grading keeps the reply path fast"
    st.thread_deltas = [
        _delta(insight, 0.45, "t1"),
        _delta(insight, 0.45, "t2", quarantined=True),
        _delta(insight, 0.45, "t3", emergent=True),
        _delta(insight, 0.8, "t4"),    # gated: already trusted, not minable
        _delta(insight, 0.45, "t5"),
    ]
    # only t1 + t5 qualify -> below min_threads -> nothing mined
    assert R.mine_delta_clusters(st) == []
    # equivalent ACTIVE belief -> cluster skipped (live reinforcement covers it)
    st.thread_deltas.append(_delta(insight, 0.45, "t6"))
    st.beliefs.beliefs.append(_belief(insight))
    assert R.mine_delta_clusters(st) == []
    print("ok: mining honors the honesty gate and skips already-held beliefs")


def test_contradiction_pairs_detects_latent_polarity_clash():
    st = ContextState()
    older = _belief("local caching always improves pipeline throughput")
    newer = _belief("local caching never improves pipeline throughput")
    agree = _belief("streaming tokens lowers perceived latency")
    st.beliefs.beliefs = [older, newer, agree]
    pairs = R.contradiction_pairs(st)
    assert len(pairs) == 1
    assert pairs[0][0] is older and pairs[0][1] is newer   # incumbent first
    print("ok: sweep finds the latent clash once, incumbent listed first")


# ---------- orchestration (temp DB + injected fake deliberation) ----------

def _temp_session(**kw):
    import tempfile, storage, mcm as M, session as S
    from pathlib import Path
    tmp = tempfile.mkdtemp(prefix="seedling_refl_")
    storage._DB_PATH = Path(tmp) / "db"; storage._db = None
    m = M.MCM(); m.restore_context(fresh=True)
    m.graceful_pause = lambda notes="": None   # keep repo snapshots/ clean in tests
    kw.setdefault("osmosis_promotion_budget", 2)
    sess = S.ThreadSession(
        mcm=m, critic=types.SimpleNamespace(evaluate=lambda u, r: None),
        model_name="m", fresh=True, deliberation_enabled=False,
        live_deliberation_enabled=False, **kw)
    sess._memory_notices = []
    return tmp, m, sess


def _fake_delib(synthesis=None, antithesis="a real objection",
                agreement=0.4, contested=False):
    def fake(text, thread_id, chat_fn, model):
        return types.SimpleNamespace(
            synthesis=(synthesis if synthesis is not None else text),
            antithesis=antithesis, agreement=agreement, contested=contested)
    return fake


def _archive_with_recurrence(m):
    b = _belief("retry backoff jitter smooths load spikes")
    b.archived, b.archived_reason = True, "low_signal"
    m._state.beliefs.archived.append(b)
    m._state.thread_deltas.append(_delta(
        "adding jitter to retry backoff smooths load spikes under contention",
        0.8, "t-prev"))
    return b


def test_parole_granted_revives_with_budget_spend():
    import shutil, storage
    tmp, m, sess = _temp_session()
    try:
        b = _archive_with_recurrence(m)
        rep = R.run_reflection(sess, max_deliberations=1,
                               _deliberate=_fake_delib(contested=False))
        assert rep.paroles_heard == 1 and rep.paroles_granted == 1
        assert b in m._state.beliefs.beliefs and not b.archived
        assert b.reinforce_count == 2      # revival is a re-earning
        assert sess._osmosis_promotions == 1
        print("ok: reconciled deliberation paroles the belief and spends budget")
    finally:
        shutil.rmtree(tmp, ignore_errors=True); storage._db = None


def test_parole_denied_stays_archived_and_is_not_reheard():
    import shutil, storage
    tmp, m, sess = _temp_session()
    try:
        b = _archive_with_recurrence(m)
        # contested AND unchanged thesis -> the objection stood
        rep = R.run_reflection(sess, max_deliberations=1,
                               _deliberate=_fake_delib(contested=True))
        assert rep.paroles_heard == 1 and rep.paroles_granted == 0
        assert b in m._state.beliefs.archived, "denied parole must stay archived"
        assert "parole_denied" in b.archived_reason
        assert sess._osmosis_promotions == 0
        # a second pass never re-spends on the same record
        rep2 = R.run_reflection(sess, max_deliberations=1,
                                _deliberate=_fake_delib(contested=True))
        assert rep2.paroles_heard == 0
        print("ok: denied parole is retained (not deleted) and never re-heard")
    finally:
        shutil.rmtree(tmp, ignore_errors=True); storage._db = None


def test_spend_cap_and_osmotic_budget_are_respected():
    import shutil, storage
    tmp, m, sess = _temp_session()
    try:
        b1 = _archive_with_recurrence(m)
        b2 = _belief("jitter on retry backoff smooths load spikes broadly")
        b2.archived, b2.archived_reason = True, "low_signal"
        m._state.beliefs.archived.append(b2)
        # spend cap: 2 candidates, 1 allowed deliberation -> 1 hearing only
        rep = R.run_reflection(sess, max_deliberations=1,
                               _deliberate=_fake_delib(contested=False))
        assert rep.deliberations_spent == 1 and rep.paroles_heard == 1
        # budget: exhaust it -> the remaining candidate is budget-blocked
        sess._osmosis_promotions = sess.osmosis_promotion_budget
        rep2 = R.run_reflection(sess, max_deliberations=5,
                                _deliberate=_fake_delib(contested=False))
        assert rep2.paroles_heard == 0 and rep2.budget_blocked >= 1
        print("ok: hard spend cap and osmotic budget both hold")
    finally:
        shutil.rmtree(tmp, ignore_errors=True); storage._db = None


def test_conflict_sweep_resolves_via_existing_machinery():
    import shutil, storage
    tmp, m, sess = _temp_session()
    try:
        older = _belief("local caching always improves pipeline throughput")
        newer = _belief("local caching never improves pipeline throughput")
        m._state.beliefs.beliefs = [older, newer]
        rep = R.run_reflection(sess, max_deliberations=1,
                               _deliberate=_fake_delib())
        assert rep.conflicts_found == 1 and rep.conflicts_resolved == 1
        active = m._state.beliefs.beliefs
        archived = m._state.beliefs.archived
        assert len(active) == 1 and len(archived) == 1
        assert archived[0].archived_reason.startswith("lost_conflict:")
        print("ok: latent contradiction resolved; loser archived with provenance")
    finally:
        shutil.rmtree(tmp, ignore_errors=True); storage._db = None


def test_mining_promotes_with_reflection_provenance():
    import shutil, storage
    tmp, m, sess = _temp_session()
    try:
        insight = "streaming tokens to the client lowers perceived latency"
        for tid in ("t1", "t2", "t3"):
            m._state.thread_deltas.append(_delta(insight, 0.45, tid))
        rep = R.run_reflection(sess, max_deliberations=1,
                               _deliberate=_fake_delib(contested=True,
                                                       synthesis=insight + ", within limits"))
        assert rep.candidates_mined == 1 and rep.candidates_promoted == 1
        b = m._state.beliefs.beliefs[0]
        assert b.source == "reflection" and b.kind == "insight"
        assert b.contested and b.dissent == "a real objection"
        assert sess._osmosis_promotions == 1
        print("ok: convergent sub-gate insights enter as auditable reflection beliefs")
    finally:
        shutil.rmtree(tmp, ignore_errors=True); storage._db = None


def test_passthrough_deliberation_changes_nothing():
    import shutil, storage
    tmp, m, sess = _temp_session()
    try:
        b = _archive_with_recurrence(m)
        rep = R.run_reflection(
            sess, max_deliberations=3,
            _deliberate=_fake_delib(antithesis="[deliberation unavailable]"))
        assert rep.paroles_heard == 0 and rep.paroles_granted == 0
        assert b in m._state.beliefs.archived
        assert "parole_denied" not in b.archived_reason, \
            "machinery failure must not count as a verdict"
        print("ok: deliberation failure is a non-event, never a verdict")
    finally:
        shutil.rmtree(tmp, ignore_errors=True); storage._db = None


if __name__ == "__main__":
    test_parole_needs_recurrence_and_clean_record()
    test_mining_requires_convergence_across_threads()
    test_mining_excludes_gated_quarantined_emergent_and_known()
    test_contradiction_pairs_detects_latent_polarity_clash()
    test_parole_granted_revives_with_budget_spend()
    test_parole_denied_stays_archived_and_is_not_reheard()
    test_spend_cap_and_osmotic_budget_are_respected()
    test_conflict_sweep_resolves_via_existing_machinery()
    test_mining_promotes_with_reflection_provenance()
    test_passthrough_deliberation_changes_nothing()
    print("\nALL OSMOSIS STEP-4 (reflection) TESTS PASSED")
