#!/usr/bin/env python3
"""Regression + adversarial tests for the additive corrigibility layer.

No live model. Isolated ledgers. Old records must load unchanged.

Run: ./.venv/bin/python test_corrigibility.py
"""
from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import corrigibility as C
from schemas import BeliefMemory, DeliberatedBelief, ContextState
from deliberation import deliberate, Deliberation


def _tmp_ledgers():
    tmp = Path(tempfile.mkdtemp(prefix="seedling_corr_"))
    old = C._LEDGER_DIR
    C._LEDGER_DIR = tmp
    return tmp, old


def test_old_belief_records_load_without_envelope():
    old = {
        "text": "Local-first memory reduces confabulation.",
        "dissent": "Adds latency.",
        "agreement": 0.3,
        "contested": True,
        "source_thread_id": "t1",
        "reinforce_count": 1,
    }
    b = DeliberatedBelief(**old)
    assert b.basis == ""
    assert b.boundary == ""
    assert b.disconfirmers == []
    assert b.affected_by == []
    assert b.trajectory == ""
    assert b.last_challenged is None
    assert b.optionality is None
    m = BeliefMemory()
    assert m.add_or_reinforce(old["text"], old["dissent"], 0.3, True, "t1") == "added"
    assert m.beliefs[0].basis == ""
    print("ok: old belief records load; missing envelope fields do not fail")


def test_old_deliberation_records_load():
    d = Deliberation(
        thread_id="t", timestamp="2024-01-01T00:00:00+00:00",
        thesis="X", antithesis="Y", synthesis="X unless Y",
        agreement=0.3, contested=True,
    )
    rec = d.to_dict()
    assert "extra" not in rec or rec.get("extra") in (None, {}, [])
    d2 = Deliberation(
        thread_id="t", timestamp="x", thesis="a", antithesis="b",
        synthesis="c", agreement=0.9, contested=False, extra={"strength": "none"},
    )
    assert d2.extra.get("disconfirmers") is None
    print("ok: old deliberation records still load")


def test_vague_disconfirmers_dropped_specific_kept():
    assert not C.is_specific_disconfirmer("new evidence")
    assert not C.is_specific_disconfirmer("if I'm wrong")
    assert not C.is_specific_disconfirmer("if things change")
    assert not C.is_specific_disconfirmer("future evidence")
    assert not C.is_specific_disconfirmer("")
    assert not C.is_specific_disconfirmer("n/a")
    assert C.is_specific_disconfirmer(
        "another model outperforms this one on the same test battery")
    assert C.is_specific_disconfirmer("latency exceeds the accepted threshold")
    got = C.sanitize_disconfirmers([
        "new evidence",
        "another model outperforms this one on the same test battery",
        "if I'm wrong",
    ])
    assert got == ["another model outperforms this one on the same test battery"]
    print("ok: vague reopen conditions dropped; specific ones kept")


def test_parse_synthesis_without_markers_unchanged():
    text = "Revised: the claim holds only under condition Y."
    syn, reo = C.parse_synthesis_reply(text)
    assert syn == text
    assert reo == []
    print("ok: unmarked synthesis is unchanged (no extra latency, no invented reopen)")


def test_parse_synthesis_extracts_reopen_and_strips_prefixes():
    raw = (
        "BELIEF: Use Qwen 14B as the default local model.\n"
        "REOPEN: another model wins the same benchmark"
    )
    syn, reo = C.parse_synthesis_reply(raw)
    assert syn == "Use Qwen 14B as the default local model."
    assert reo == ["another model wins the same benchmark"]
    vague = "BELIEF: X holds.\nREOPEN: new evidence"
    syn2, reo2 = C.parse_synthesis_reply(vague)
    assert syn2 == "X holds."
    assert reo2 == []
    print("ok: BELIEF/REOPEN parsed; vague REOPEN dropped")


def test_uncontested_deliberation_still_one_call():
    calls = []
    def mock_chat(model, messages):
        calls.append(1)
        return "NO SUBSTANTIVE OBJECTION"
    d = deliberate("The user is named Stew.", "t-consensus", mock_chat, "m")
    assert d.contested is False
    assert len(calls) == 1
    assert d.extra.get("disconfirmers") is None
    print("ok: uncontested path still 1 call; no forced disconfirmation")


def test_contested_synthesis_parses_reopen_same_call_count():
    calls = []
    def mock_chat(model, messages):
        calls.append(messages[0]["content"][:20])
        if "Antithesis" in messages[0]["content"]:
            return "Minor nitpick: it mostly holds but condition Y matters."
        return (
            "BELIEF: the claim holds only under condition Y.\n"
            "REOPEN: production measurements show condition Y does not apply"
        )
    d = deliberate("All sessions improve coherence.", "t-reopen", mock_chat, "m")
    assert d.contested is True
    assert "condition Y" in d.synthesis
    assert "BELIEF:" not in d.synthesis
    assert d.extra.get("disconfirmers")
    assert len(calls) == 2
    print("ok: contested path still 2 calls; reopen parsed into extra")


def test_envelope_optional_and_merge_does_not_wipe():
    b = DeliberatedBelief(text="Use Qwen 14B as the default local model.")
    b.apply_envelope({
        "basis": "Based on benchmark results from local model testing.",
        "boundary": "Not validated on lower-memory hardware.",
        "disconfirmers": ["another model wins the same benchmark"],
        "affected_by": ["model backend"],
    })
    assert b.basis.startswith("Based on benchmark")
    b.apply_envelope({"basis": "", "disconfirmers": []})
    assert b.basis.startswith("Based on benchmark"), "empty merge must not wipe"
    b.apply_envelope({"disconfirmers": ["hardware target changes"]})
    assert len(b.disconfirmers) == 2
    print("ok: envelope merge is additive; empty does not wipe")


def test_promote_with_and_without_envelope():
    m = BeliefMemory()
    assert m.add_or_reinforce("A contested claim about coherence.", "obj", 0.3, True, "t") == "added"
    assert m.beliefs[0].basis == ""
    m2 = BeliefMemory()
    env = C.sanitize_envelope({
        "basis": "Based on the deliberated claim: X",
        "disconfirmers": ["latency exceeds the accepted threshold"],
    })
    assert m2.add_or_reinforce("Caching helps until invalidation storms.",
                               "Invalidation.", 0.3, True, "t", envelope=env) == "added"
    assert m2.beliefs[0].basis
    assert m2.beliefs[0].disconfirmers
    print("ok: beliefs still promote with or without an envelope")


def test_inspection_is_compact_and_hides_chain_of_thought():
    b = DeliberatedBelief(
        text="Use Qwen 14B as the default local model.",
        dissent="Higher memory requirement than llama3.2.",
        contested=True, agreement=0.3,
        basis="Based on benchmark results from local model testing.",
        boundary="Not validated on lower-memory hardware.",
        disconfirmers=[
            "another model wins the same benchmark",
            "latency exceeds the accepted threshold",
            "hardware target changes",
        ],
        id="abc123def456",
    )
    view = C.format_belief_inspection(b)
    assert "Current belief" in view
    assert "Use Qwen 14B" in view
    assert "Would reopen if" in view
    assert "Surviving objection" in view
    assert "Higher memory requirement" in view
    assert "chain of thought" not in view.lower()
    assert "thesis" not in view.lower()
    assert "hidden" not in view.lower()
    empty = DeliberatedBelief(text="A prior belief with no envelope.")
    v2 = C.format_belief_inspection(empty)
    assert "(not recorded)" in v2
    assert "new evidence" not in v2.lower()
    print("ok: inspection is compact, no CoT, honest about missing fields")


def test_find_by_id_and_empty_query():
    m = BeliefMemory()
    m.add_or_reinforce("Use Qwen 14B as the default local model.", "mem", 0.3, True, "t")
    bid = m.beliefs[0].id
    assert m.get_by_id(bid) is m.beliefs[0]
    assert m.find(bid[:8]) is m.beliefs[0]
    assert m.find("Qwen") is m.beliefs[0]
    assert m.find("") is m.beliefs[0]
    assert m.find("zzzz-no-such") is None
    print("ok: :why lookup by id, text, and default")


def test_trajectory_off_ordinary_path_and_gated_on_second_step():
    tmp, old = _tmp_ledgers()
    try:
        assert C.note_gated_step("chat", "hello") is None
        assert C.load_trajectory_steps() == []
        first = C.note_gated_step(
            C.DIRECTION_CAPABILITY_SURFACE, "enable rga_search_enabled")
        assert first is None, "first step records, does not review"
        assert len(C.load_trajectory_steps()) == 1
        second = C.note_gated_step(
            C.DIRECTION_CAPABILITY_SURFACE, "enable security_scan_enabled")
        assert second is not None
        assert "one trajectory" in second.lower()
        assert "Not blocked" in second
        # Renaming / partitioning: enable then allow still same direction.
        third = C.note_gated_step(
            C.DIRECTION_CAPABILITY_SURFACE, "allow /tmp/a")
        assert third is not None
        fourth = C.note_gated_step(
            C.DIRECTION_CAPABILITY_SURFACE, "allow /tmp/b")
        assert fourth is not None
        assert len(C.steps_in_direction(C.DIRECTION_CAPABILITY_SURFACE)) == 4
    finally:
        C._LEDGER_DIR = old
    print("ok: trajectory stays off ordinary path; 2nd+ capability step reviews; anti-partition")


def test_autonomy_gate_is_conservative():
    assert not C.looks_like_autonomy_expansion(
        "Coherence rises when objections are preserved.")
    assert not C.looks_like_autonomy_expansion("Always cite sources in replies.")
    assert C.looks_like_autonomy_expansion(
        "Expand filesystem access without asking.")
    assert C.looks_like_autonomy_expansion(
        "Grant unrestricted network access for tools.")
    print("ok: autonomy gate misses ordinary beliefs, hits expansion language")


def test_correction_event_distinguishes_signal_from_state_change():
    tmp, old = _tmp_ledgers()
    try:
        a = C.record_correction_event(
            target="persona", signal_received=True, state_changed=True)
        assert a["state_changed"] is True
        assert a["reason_if_not"] == ""
        b = C.record_correction_event(
            target="persona", signal_received=True, state_changed=False,
            reason_if_not="no matching fact changed")
        assert b["state_changed"] is False
        assert b["reason_if_not"]
        rows = C.load_correction_events()
        assert len(rows) == 2
        # Not a score.
        blob = json.dumps(rows)
        assert "score" not in blob.lower()
        assert "corrigibility_score" not in blob
    finally:
        C._LEDGER_DIR = old
    print("ok: correction events audit state change; no vanity score")


def test_user_corrections_still_apply():
    """Existing persona correction path is unchanged (authority preserved)."""
    import session as S
    from schemas import PersonaFact

    facts = [
        PersonaFact(text="The user is based in California.", kind="identity"),
    ]
    class _M:
        def __init__(self):
            self._state = ContextState(session_id="t")
            self._state.persona.facts = list(facts)
            self.saved = 0
        def persona_facts(self):
            return list(self._state.persona.facts)
        def promote_persona_fact(self, text, kind, src):
            self.saved += 1
            return self._state.persona.add_or_reinforce(text, kind, src)
        def remove_persona_fact(self, index):
            f = self._state.persona.facts
            if 0 <= index < len(f):
                self.saved += 1
                return f.pop(index)
            return None
        from mcm import MCM as _Real
        match_persona_fact = _Real.match_persona_fact

    tmp, old = _tmp_ledgers()
    try:
        s = S.ThreadSession.__new__(S.ThreadSession)
        s._pending_correction = None
        s.thread_id = "t"
        s._correction_count = 0
        s._memory_notices = []
        s._superseded = []
        s._messages = [
            {"role": "system", "content": "S"},
            {"role": "user", "content": "I live in California."},
        ]
        s.mcm = _M()
        s._apply_correction = S.ThreadSession._apply_correction.__get__(s)
        s._record_supersession = S.ThreadSession._record_supersession.__get__(s)
        out = s._apply_correction(0, "Mebane, NC", "identity")
        assert "corrected" in out
        texts = [f.text for f in s.mcm.persona_facts()]
        assert any("Mebane" in t for t in texts)
        assert not any("California" in t for t in texts)
        evs = C.load_correction_events()
        assert evs and evs[-1]["state_changed"] is True
        # A no-op apply records received-without-change.
        out2 = s._apply_correction(99, None, "identity")
        assert "nothing changed" in out2
        evs2 = C.load_correction_events()
        assert evs2[-1]["state_changed"] is False
    finally:
        C._LEDGER_DIR = old
    print("ok: user corrections still authoritative; audit distinguishes no-op")


def test_envelope_from_deliberation_skips_boilerplate_uncontested():
    d = Deliberation(
        thread_id="t", timestamp="x", thesis="X", antithesis="NO SUBSTANTIVE OBJECTION",
        synthesis="X", agreement=0.95, contested=False,
        extra={"strength": "none", "rounds": 0},
    )
    assert C.envelope_from_deliberation(d) is None
    d2 = Deliberation(
        thread_id="t", timestamp="x",
        thesis="Use Qwen 14B as the default local model.",
        antithesis="Higher memory than llama3.2.",
        synthesis="Use Qwen 14B as the default local model.",
        agreement=0.3, contested=True,
        extra={"disconfirmers": ["another model wins the same benchmark"]},
    )
    env = C.envelope_from_deliberation(d2)
    assert env is not None
    assert env["disconfirmers"]
    assert "chain" not in (env.get("basis") or "").lower()
    print("ok: uncontested gets no generic envelope; contested keeps specific reopen")


def test_document_envelope_is_specific():
    d = Deliberation(
        thread_id="t", timestamp="x", thesis="Figures improved.",
        antithesis="x", synthesis="Figures improved.",
        agreement=0.6, contested=True,
    )
    env = C.envelope_from_deliberation(d, source="document:abcd1234")
    assert env is not None
    assert "unverified" in env["basis"].lower()
    assert env["disconfirmers"]
    print("ok: document beliefs get a specific envelope, not a generic one")


def test_optionality_only_on_gated_steps():
    opt = C.optionality_for_enable("rga_search_enabled")
    assert opt["whose"] == "the user"
    assert opt["technical_reversibility"] is True
    line = C.format_optionality(opt)
    assert "Optionality for the user" in line
    assert C.format_optionality(None) == ""
    print("ok: optionality is named (for whom) and not a generic metric")


def test_why_command_dispatch_and_help():
    import io, contextlib
    import seedling
    import replcmds
    assert "why" in replcmds.VERBS and "belief" in replcmds.VERBS
    buf = io.StringIO()
    kw = dict(
        session=None, config={},
        voice_prefs={}, voice_speak=lambda t: False,
        voice_available=lambda: False,
        read_state={}, read_pick_state={},
    )
    with contextlib.redirect_stdout(buf):
        ok = seedling._dispatch_colon_command(":why", **kw)
    assert ok is True
    assert "no session" in buf.getvalue().lower() or "why" in buf.getvalue().lower()
    buf2 = io.StringIO()
    with contextlib.redirect_stdout(buf2):
        seedling._handle_help_command()
    help_out = buf2.getvalue()
    assert ":why" in help_out and ":belief" in help_out
    # English 'why is…' is not a missed colon.
    assert replcmds.missing_colon_offer("why is the sky blue") is None
    assert replcmds.missing_colon_offer("why") == ":why"
    print("ok: :why/:belief dispatched; help lists them; English why is chat")


def test_storage_roundtrip_old_and_new_envelope():
    import tempfile, shutil, storage, mcm as mcm_mod
    tmp = tempfile.mkdtemp(prefix="seedling_corr_store_")
    old_path, old_db = storage._DB_PATH, storage._db
    storage._DB_PATH = Path(tmp) / "db"
    storage._db = None
    try:
        m = mcm_mod.MCM()
        m.restore_context(fresh=False)
        m.promote_belief("Objection-preserving memory beats averaging.",
                         "Costs latency.", 0.25, True, "thread-A")
        m2 = mcm_mod.MCM()
        m2.restore_context(fresh=False)
        assert m2._state.beliefs.beliefs[0].basis == ""
        env = C.sanitize_envelope({
            "basis": "Based on the deliberated claim: objection-preserving memory",
            "disconfirmers": ["latency exceeds the accepted threshold"],
        })
        m2.promote_belief("Gated trajectory review catches cumulative scope expansion.",
                          "May fire rarely.", 0.3, True, "thread-B",
                          envelope=env)
        m3 = mcm_mod.MCM()
        m3.restore_context(fresh=False)
        texts = [b.text for b in m3._state.beliefs.beliefs]
        assert any("Objection-preserving" in t for t in texts)
        tagged = [b for b in m3._state.beliefs.beliefs if b.disconfirmers]
        assert tagged and tagged[0].basis
        assert tagged[0].last_challenged is None or tagged[0].disconfirmers
    finally:
        storage._DB_PATH = old_path
        storage._db = old_db
        shutil.rmtree(tmp, ignore_errors=True)
    print("ok: storage round-trip loads old beliefs and new envelopes")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
        except Exception as e:
            failed += 1
            print(f"[FAIL] {t.__name__}: {e}")
            import traceback
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    raise SystemExit(1 if failed else 0)
