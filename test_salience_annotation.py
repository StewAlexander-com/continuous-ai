#!/usr/bin/env python3
"""Tests for the three 'missing layers':
  1. per-record salience (kind priors, update_salience, signal folding, round-trip)
  2. mid-response [REMEMBER] self-annotation (parse, doubt-scope gate, strip)
  3. salience + keyword-boost retrieval (query re-ranking)

Schema/parse tests are model-free; storage round-trip needs lancedb (venv).
Run: ./.venv/bin/python test_salience_annotation.py
"""
import sys, types
if "ollama" not in sys.modules:
    sys.modules["ollama"] = types.ModuleType("ollama")

from schemas import (BeliefMemory, DeliberatedBelief, default_salience,
                     VALID_BELIEF_KINDS)
import session as S


# ---------- Feature 1: salience ----------
def test_salience_defaults_by_kind():
    assert default_salience("value") == 0.95
    assert default_salience("episode_summary") == 0.5
    assert default_salience("unknown_kind") == 0.6
    v = DeliberatedBelief(text="x", kind="value")
    assert v.effective_salience() == 0.95
    print("ok: salience priors seed from kind")


def test_salience_boosts_signal_ordering():
    now = None
    val = DeliberatedBelief(text="core value", kind="value", contested=True, agreement=0.4)
    epi = DeliberatedBelief(text="recent episode", kind="episode_summary", contested=True, agreement=0.4)
    # same recency/contested, but the value kind outranks the episode on salience
    assert val.signal_score() > epi.signal_score()
    print("ok: higher salience -> higher signal (core value outranks episode)")


def test_update_salience_clamped_and_targeted():
    m = BeliefMemory()
    m.add_or_reinforce("A belief about coherence preservation.", "obj", 0.3, True, "t1")
    bid = m.beliefs[0].id
    assert m.update_salience(bid, +0.1) and abs(m.beliefs[0].effective_salience() - 0.7) < 1e-9
    assert m.update_salience(bid, +5.0) and m.beliefs[0].effective_salience() == 1.0   # clamp hi
    assert m.update_salience(bid, -5.0) and m.beliefs[0].effective_salience() == 0.0   # clamp lo
    assert m.update_salience("no-such-id", 0.1) is False
    print("ok: update_salience targets by id, clamps [0,1], rejects unknown id")


# ---------- Feature 2: [REMEMBER] ----------
def test_remember_parse_and_strip():
    txt = ("Here is my answer.\n"
           "[REMEMBER kind=preference subject=\"music\"] The user enjoys chill electronica. [/REMEMBER]\n"
           "Hope that helps.")
    clean, anns = S._parse_remember_tags(txt)
    assert "[REMEMBER" not in clean and "chill electronica" not in clean
    assert "Here is my answer." in clean and "Hope that helps." in clean
    assert len(anns) == 1 and anns[0]["kind"] == "preference" and anns[0]["subject"] == "music"
    print("ok: [REMEMBER] parsed, attributes read, tags stripped from display text")


def test_remember_stream_filter_hides_block():
    # token-by-token, the filter must not emit anything inside the block
    shown = []
    f = S._RememberStreamFilter(lambda s: shown.append(s))
    for tok in ["Visible ", "start. ", "[REMEM", "BER kind=insight] secret ", "note [/REMEM", "BER] visible ", "end."]:
        f(tok)
    f.flush()    # end-of-stream, as the real chat loop does
    out = "".join(shown)
    assert "secret" not in out and "note" not in out, f"block leaked: {out!r}"
    assert "Visible start." in out and "visible end." in out
    print("ok: streaming filter suppresses [REMEMBER] block across token boundaries")


def test_remember_rejects_user_fact_via_guard():
    # the doubt-scope guard must reject a [REMEMBER] asserting a user fact
    import tempfile, shutil, storage, mcm as M
    from pathlib import Path
    tmp = tempfile.mkdtemp(prefix="seedling_ann_")
    storage._DB_PATH = Path(tmp) / "db"; storage._db = None
    try:
        m = M.MCM(); m.restore_context(fresh=True)
        sess = S.ThreadSession(mcm=m, critic=types.SimpleNamespace(evaluate=lambda u, r: None),
                               model_name="m", fresh=True, deliberation_enabled=False,
                               live_deliberation_enabled=False, live_annotation_enabled=True)
        sess._memory_notices = []
        # 1) a legit model insight -> stored as a belief (source inferred)
        sess._process_annotations([{"kind": "insight", "subject": "x",
                                    "content": "Streaming reduces perceived latency materially."}])
        # 2) a user-fact assertion -> REJECTED, not stored
        sess._process_annotations([{"kind": "preference", "subject": "y",
                                    "content": "The user lives in Mebane and is named Stew."}])
        texts = [b.text for b in m._state.beliefs.beliefs]
        assert any("Streaming reduces" in t for t in texts), "legit insight should be stored"
        assert not any("Mebane" in t for t in texts), "user-fact [REMEMBER] must be rejected"
        # the stored insight carries source='inferred'
        b = next(b for b in m._state.beliefs.beliefs if "Streaming reduces" in b.text)
        assert b.source == "inferred" and b.kind == "insight"
        print("ok: [REMEMBER] stores model insights (source=inferred), rejects user-fact assertions")
    finally:
        shutil.rmtree(tmp, ignore_errors=True); storage._db = None


def test_remember_rejects_unknown_kind():
    import tempfile, shutil, storage, mcm as M
    from pathlib import Path
    tmp = tempfile.mkdtemp(prefix="seedling_ann2_")
    storage._DB_PATH = Path(tmp) / "db"; storage._db = None
    try:
        m = M.MCM(); m.restore_context(fresh=True)
        sess = S.ThreadSession(mcm=m, critic=types.SimpleNamespace(evaluate=lambda u, r: None),
                               model_name="m", fresh=True, deliberation_enabled=False,
                               live_deliberation_enabled=False, live_annotation_enabled=True)
        sess._memory_notices = []
        sess._process_annotations([{"kind": "nonsense", "subject": "z",
                                    "content": "Some content with an invalid kind."}])
        assert len(m._state.beliefs.beliefs) == 0, "unknown kind must be rejected"
        print("ok: [REMEMBER] with an invalid kind is rejected")
    finally:
        shutil.rmtree(tmp, ignore_errors=True); storage._db = None


# ---------- Feature 3: keyword-boost retrieval ----------
def test_keyword_boost_reranks_comparable_beliefs():
    m = BeliefMemory()
    m.add_or_reinforce("Streaming responses improves perceived latency.", "obj", 0.3, True, "t1", kind="insight")
    m.add_or_reinforce("Background grading keeps the reply path fast.", "obj", 0.3, True, "t2", kind="insight")
    top_q = m.render(query="why is grading in the background").splitlines()[0]
    assert "grading" in top_q, "keyword boost should surface the query-relevant belief"
    # no query -> deterministic signal order (no boost applied)
    assert m.render(query="") == m.render()
    print("ok: keyword boost re-ranks comparable beliefs; no query => unchanged order")


# ---------- round-trip ----------
def test_salience_fields_round_trip():
    import tempfile, shutil, storage, mcm as M
    from pathlib import Path
    tmp = tempfile.mkdtemp(prefix="seedling_sal_")
    storage._DB_PATH = Path(tmp) / "db"; storage._db = None
    try:
        m = M.MCM(); m.restore_context(fresh=True)
        m.promote_belief("A principle worth keeping.", "obj", 0.25, True, "t1",
                         kind="principle", source="deliberation")
        bid = m._state.beliefs.beliefs[0].id
        m.update_salience(bid, +0.05)
        m2 = M.MCM(); m2.restore_context(fresh=False)
        b = m2._state.beliefs.beliefs[0]
        assert b.kind == "principle" and b.source == "deliberation"
        assert b.id == bid, "stable id survives reload"
        assert abs(b.effective_salience() - 0.9) < 1e-9, "salience persisted (0.85 + 0.05)"
        print("ok: salience/kind/source/id round-trip through storage")
    finally:
        shutil.rmtree(tmp, ignore_errors=True); storage._db = None


if __name__ == "__main__":
    test_salience_defaults_by_kind()
    test_salience_boosts_signal_ordering()
    test_update_salience_clamped_and_targeted()
    test_remember_parse_and_strip()
    test_remember_stream_filter_hides_block()
    test_remember_rejects_user_fact_via_guard()
    test_remember_rejects_unknown_kind()
    test_keyword_boost_reranks_comparable_beliefs()
    test_salience_fields_round_trip()
    print("\nALL SALIENCE/ANNOTATION/RETRIEVAL TESTS PASSED")
