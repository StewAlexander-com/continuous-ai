#!/usr/bin/env python3
"""Tests for document osmosis (Step 5): beliefs formed while an attached file
is in the model window carry 'document:<hash>' provenance, enter contested-
by-construction, consume the osmotic budget, and are retractable in one
auditable, revivable sweep. Persona is structurally untouched. The kill-switch
restores pre-Step-5 behavior exactly.

Temp-DB session shims; no model needed.
Run: ./.venv/bin/python test_document_osmosis.py
"""
import sys, types
if "ollama" not in sys.modules:
    sys.modules["ollama"] = types.ModuleType("ollama")

from datetime import datetime, timezone

from schemas import BeliefMemory, DeliberatedBelief
import session as S


def _temp_session(**kw):
    import tempfile, storage, mcm as M
    from pathlib import Path
    tmp = tempfile.mkdtemp(prefix="seedling_doc_")
    storage._DB_PATH = Path(tmp) / "db"; storage._db = None
    m = M.MCM(); m.restore_context(fresh=True)
    kw.setdefault("document_osmosis_enabled", True)
    kw.setdefault("osmosis_promotion_budget", 2)
    sess = S.ThreadSession(
        mcm=m, critic=types.SimpleNamespace(evaluate=lambda u, r: None),
        model_name="m", fresh=True, deliberation_enabled=False,
        live_deliberation_enabled=False, **kw)
    sess._memory_notices = []
    return tmp, m, sess


def _attach(sess, name="report.pdf", body="Quarterly figures improved."):
    sess._messages.append({"role": "user", "content":
        f"[USER-ATTACHED FILE: {name}]\n{body}\n\nThe user attached {name}."})


def _delib(text, contested=False, antithesis="", agreement=0.9):
    return types.SimpleNamespace(synthesis=text, antithesis=antithesis,
                                 agreement=agreement, contested=contested)


def test_doc_hash_is_stable_and_window_scoped():
    tmp, m, sess = _temp_session()
    import shutil, storage
    try:
        assert sess._active_doc_hash() is None
        _attach(sess, "report.pdf")
        h = sess._active_doc_hash()
        assert h == S._doc_hash("report.pdf") and len(h) == 8
        # a later attach wins (most recent document in the window)
        _attach(sess, "notes.docx")
        assert sess._active_doc_hash() == S._doc_hash("notes.docx")
        # once the attach scrolls out of the model window, no provenance
        sess._history_window_turns = 2
        sess._messages.extend([{"role": "user", "content": "unrelated"},
                               {"role": "assistant", "content": "reply"}])
        assert sess._active_doc_hash() is None
        print("ok: provenance hash is stable, latest-attach wins, window-scoped")
    finally:
        shutil.rmtree(tmp, ignore_errors=True); storage._db = None


def test_document_belief_carries_provenance_and_default_dissent():
    tmp, m, sess = _temp_session()
    import shutil, storage
    try:
        _attach(sess, "report.pdf")
        sess._promote_belief_from_delib(
            _delib("The quarterly figures show real improvement."))
        b = m._state.beliefs.beliefs[0]
        assert b.source == f"document:{S._doc_hash('report.pdf')}"
        # contested-by-construction: unverified single source
        assert b.contested and b.dissent == S._DOC_DEFAULT_DISSENT
        assert b.agreement <= 0.6
        # a deliberation that ALREADY surfaced a real objection keeps its own
        sess._promote_belief_from_delib(
            _delib("Marketing spend drove most of the gain.",
                   contested=True, antithesis="Attribution is confounded.",
                   agreement=0.3))
        b2 = m._state.beliefs.beliefs[1]
        assert b2.dissent == "Attribution is confounded." and b2.agreement == 0.3
        print("ok: document beliefs are tagged + contested-by-construction")
    finally:
        shutil.rmtree(tmp, ignore_errors=True); storage._db = None


def test_document_channel_is_budgeted_conversation_is_not():
    tmp, m, sess = _temp_session()
    import shutil, storage
    try:
        _attach(sess, "report.pdf")
        sess._promote_belief_from_delib(_delib("Figures improved across regions."))
        sess._promote_belief_from_delib(_delib("Costs fell due to renegotiated contracts."))
        assert len(m._state.beliefs.beliefs) == 2
        # 3rd document belief -> deferred by the shared osmotic budget
        sess._promote_belief_from_delib(_delib("Headcount stayed entirely flat."))
        assert len(m._state.beliefs.beliefs) == 2
        # plain CONVERSATION deliberation stays exempt even with budget spent
        sess._messages.clear()   # no attach in window anymore
        sess._promote_belief_from_delib(
            _delib("Contested beliefs carry more information than consensus."))
        assert len(m._state.beliefs.beliefs) == 3
        assert m._state.beliefs.beliefs[-1].source == "deliberation"
        print("ok: document inflow is budgeted; conversation deliberation exempt")
    finally:
        shutil.rmtree(tmp, ignore_errors=True); storage._db = None


def test_kill_switch_restores_pre_step5_behavior():
    tmp, m, sess = _temp_session(document_osmosis_enabled=False)
    import shutil, storage
    try:
        _attach(sess, "report.pdf")
        sess._promote_belief_from_delib(_delib("Figures improved across regions."))
        b = m._state.beliefs.beliefs[0]
        assert b.source == "deliberation" and not b.contested
        assert sess._osmosis_promotions == 0
        print("ok: kill-switch off => exactly the old promotion behavior")
    finally:
        shutil.rmtree(tmp, ignore_errors=True); storage._db = None


def test_quarantine_source_sweeps_only_that_document():
    m = BeliefMemory()
    now = datetime.now(timezone.utc)
    doc_a = DeliberatedBelief(text="figures improved", source="document:aaaa1111",
                              last_seen_at=now)
    doc_b = DeliberatedBelief(text="costs fell sharply", source="document:bbbb2222",
                              last_seen_at=now)
    conv = DeliberatedBelief(text="streaming lowers latency", source="deliberation",
                             last_seen_at=now)
    m.beliefs = [doc_a, doc_b, conv]
    moved = m.quarantine_source("document:aaaa1111")
    assert moved == [doc_a]
    assert doc_a in m.archived and doc_a.archived_reason == "source_quarantined:document:aaaa1111"
    assert doc_b in m.beliefs and conv in m.beliefs
    # 'document:' retracts ALL document-sourced beliefs, never conversation ones
    moved = m.quarantine_source("document:")
    assert moved == [doc_b] and conv in m.beliefs
    # non-regressive: retracted, not destroyed -- re-earning revives
    assert m.revive_if_present("figures improved", "t9")
    print("ok: one sweep retracts one document, auditable and revivable")


def test_source_quarantined_beliefs_are_not_paroled():
    from schemas import ContextState, ThreadDelta
    import reflection as R
    st = ContextState()
    b = DeliberatedBelief(text="retry backoff jitter smooths load spikes",
                          source="document:aaaa1111")
    st.beliefs.beliefs = [b]
    st.beliefs.quarantine_source("document:aaaa1111")
    st.thread_deltas = [ThreadDelta(
        thread_id="t1", coherence_score=0.8,
        insight_gained="adding jitter to retry backoff smooths load spikes under contention")]
    assert R.parole_candidates(st) == [], \
        "a user-retracted source must not sneak back via parole"
    print("ok: reflection parole never overrides a source retraction")


def test_persona_is_structurally_untouched():
    tmp, m, sess = _temp_session()
    import shutil, storage
    try:
        _attach(sess, "report.pdf")
        sess._promote_belief_from_delib(_delib("Figures improved across regions."))
        assert m._state.persona.facts == [], \
            "document osmosis must never write persona (user-owned truth)"
        print("ok: persona untouched by document osmosis")
    finally:
        shutil.rmtree(tmp, ignore_errors=True); storage._db = None


if __name__ == "__main__":
    test_doc_hash_is_stable_and_window_scoped()
    test_document_belief_carries_provenance_and_default_dissent()
    test_document_channel_is_budgeted_conversation_is_not()
    test_kill_switch_restores_pre_step5_behavior()
    test_quarantine_source_sweeps_only_that_document()
    test_source_quarantined_beliefs_are_not_paroled()
    test_persona_is_structurally_untouched()
    print("\nALL OSMOSIS STEP-5 (document osmosis) TESTS PASSED")
