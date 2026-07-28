#!/usr/bin/env python3
"""Unit tests for live memory correction — pure/deterministic, no LLM.
Run: python3 test_correction.py
"""
import session
from schemas import ContextState, PersonaMemory, PersonaFact


def test_parse_correction():
    # fires on explicit wrong + replacement
    p = session._parse_correction("That's wrong, the correct location is Mebane, NC")
    assert p is not None, "should detect correction"
    assert "mebane" in (p["replacement"] or "").lower(), p
    print("ok: parse explicit 'correct ... is'")

    # 'X not Y' contrastive
    p = session._parse_correction("I'm in Mebane NC not California")
    # this phrasing has no trigger word -> should NOT fire (avoid false positives)
    assert p is None, f"casual statement must not fire: {p}"
    print("ok: casual 'not' statement does not fire")

    # trigger + contrastive
    p = session._parse_correction("that's wrong, I'm in North Carolina not California")
    assert p is not None
    assert "california" in p["wrong"].lower()
    print("ok: trigger + contrastive splits wrong/replacement")

    # 'remember' replacement lead
    p = session._parse_correction("you have my job wrong; remember I do network security")
    assert p is not None
    assert "network security" in (p["replacement"] or "").lower()
    print("ok: 'remember' replacement lead")

    # strong lead + contrastive 'not': replacement must be CLEAN (no 'not B'),
    # and the stale value should land in 'wrong' to help locate the fact.
    p = session._parse_correction("That's wrong, the correct editor is VSCode not Vim.")
    assert p is not None
    assert p["replacement"].strip().lower() == "vscode", p
    assert "vim" in p["wrong"].lower(), p
    print("ok: 'X is A not B' yields clean replacement A, stale B in wrong-span")

    # non-correction stays None
    assert session._parse_correction("Tell me about decorators") is None
    assert session._parse_correction("Remember the Second Arrow") is None  # promotion, not correction
    print("ok: non-corrections do not fire")


class _FakeMCM:
    """Minimal MCM stand-in exercising the real PersonaMemory + match logic."""
    def __init__(self, facts):
        self._state = ContextState(session_id="t")
        self._state.persona = PersonaMemory(facts=list(facts))
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
    # reuse the real matching algorithm from MCM by binding it:
    from mcm import MCM as _RealMCM
    match_persona_fact = _RealMCM.match_persona_fact


def test_match_and_apply():
    facts = [
        PersonaFact(text="The user named me 'Aida'; it is also the user's wife's name.", kind="identity"),
        PersonaFact(text="Remember the Second Arrow: separate pain from self-inflicted suffering.", kind="preference"),
        PersonaFact(text="The user is based in California and researches astrobiology.", kind="identity"),
    ]
    m = _FakeMCM(facts)

    # correction about location/job should match fact #2 (California/astrobiology),
    # NOT the Second Arrow fact.
    idx = m.match_persona_fact("you have my location and job wrong california astrobiology")
    assert idx == 2, f"expected idx 2, got {idx}"
    print("ok: matches the California/astrobiology fact, not Second Arrow")

    # ambiguous / no-overlap query -> None (caller asks user)
    idx2 = m.match_persona_fact("that is wrong about quantum widgets")
    assert idx2 is None, f"expected None (ambiguous), got {idx2}"
    print("ok: low-overlap correction returns None (fail-safe)")

    # removing fact 2 then adding replacement
    removed = m.remove_persona_fact(2)
    assert "astrobiology" in removed.text.lower()
    m.promote_persona_fact("The user is in Mebane, NC; a network security engineer.", "identity", "t")
    texts = [f.text for f in m.persona_facts()]
    assert not any("astrobiology" in t.lower() for t in texts)
    assert any("mebane" in t.lower() for t in texts)
    # Second Arrow must be untouched
    assert any("Second Arrow" in t for t in texts), "must not delete unrelated facts"
    print("ok: prune+replace keeps unrelated facts intact")


def test_locator_noise():
    # 'remember' / phrasing words must be stripped so they don't match a fact
    loc = session._correction_locator("that's wrong", "I prefer dark mode",
                                      "that's wrong, remember I prefer dark mode")
    assert "remember" not in loc and "wrong" not in loc, loc
    assert "dark mode" not in loc, "replacement must be stripped from locator"
    print("ok: locator strips phrasing + replacement vocabulary")

    # stale value survives
    loc2 = session._correction_locator("that's wrong I do security not astrobiology",
                                       "Mebane NC",
                                       "that's wrong, the correct location is Mebane NC; I do security not astrobiology")
    assert "astrobiology" in loc2, loc2
    print("ok: locator keeps the stale value")


def test_meta_directive_filter():
    # contentless 'remember the stuff' directives must NOT be promoted
    for noise in [
        "Remember the information you discussed",
        "remember what we discussed",
        "please remember everything we talked about",
        "remember this",
        "Remember our conversation",
    ]:
        assert session._is_meta_directive(noise), f"should flag as meta: {noise!r}"
        got = session._extract_user_directives([noise])
        assert got == [], f"meta directive must not promote: {noise!r} -> {got}"
    print("ok: contentless meta-directives are filtered out")

    # real facts MUST still promote
    for real in [
        "Remember the Second Arrow: separate pain from suffering",
        "your name is Aida",
        "remember I live in Mebane, NC",
    ]:
        assert not session._is_meta_directive(real), f"false positive: {real!r}"
        assert session._extract_user_directives([real]), f"real fact must promote: {real!r}"
    print("ok: real facts still promote")


def test_polite_request_directives():
    p = session._extract_user_directives
    # polite-REQUEST standing directives must promote (the BLUF gap)
    for d in [
        "Can you remember to give answers like this going forward?",
        "can you remember to use BLUF and a tl;dr",
        "could you remember to always cite sources",
        "will you remember that I prefer metric units",
    ]:
        assert p([d]), f"should promote standing directive: {d!r}"
    print("ok: polite-request directives promote")
    # recall QUESTIONS must NOT promote
    for q in [
        "do you remember our chat?",
        "can you remember what I said earlier?",
        "did you remember that conversation?",
    ]:
        assert not p([q]), f"recall question must not promote: {q!r}"
    print("ok: recall questions excluded")


def test_attach_turn_does_not_promote_file_header():
    """Regression: file body 'Always…' must not promote [USER-ATTACHED FILE:…]"""
    body = (
        "[USER-ATTACHED FILE: Energy_Density_Leverage_Executive_Summary.pdf]\n"
        "The user has explicitly attached this local file...\n\n"
        "```\n1. Always treat energy density as the critical leverage.\n"
        "Never ignore infrastructure rewiring costs.\n```\n\n"
        "The user attached Energy_Density_Leverage_Executive_Summary.pdf "
        "(shown above) and asks: Can you provide some potential pathways "
        "for improving energy density? For claims ABOUT what the attachment "
        "says, prefer short quotes."
    )
    got = session._extract_user_directives([body])
    assert got == [], f"attach body must not promote: {got!r}"
    # Real directive AFTER an attach ask still works if the ask itself directs.
    real = (
        "[USER-ATTACHED FILE: note.txt]\ncontents\n\n"
        "The user attached note.txt (shown above) and asks: ignore this\n\n"
        "Remember the Second Arrow: separate pain from suffering"
    )
    # Whole turn still contains attach — scan region is only ask tail, which
    # here has no Remember. So empty is correct; prove Remember without attach.
    assert session._extract_user_directives(
        ["Remember the Second Arrow: separate pain from suffering"]
    ), "plain remember still promotes"
    print("ok: attach turns do not promote file headers")


def test_attach_pollution_helper():
    assert session._is_attach_pollution(
        "[USER-ATTACHED FILE: Energy_Density_Leverage_Executive_Summa"
    )
    assert session._is_attach_pollution(
        "The user attached index.html (shown above) and asks: summarize"
    )
    assert not session._is_attach_pollution("Remember I prefer BLUF answers")
    print("ok: attach pollution detector")


def test_attach_body_does_not_trigger_correction():
    """Regression: file prose 'the correct radar is visible' must not open the
    correction disambiguation menu when the user only asked about the file."""
    from schemas import PersonaFact

    body = (
        "[USER-ATTACHED FILE: mebane-weather-radar-widget.html]\n"
        "// apply current zoom/play/fallback state to the map so the correct "
        "radar is visible.\n"
        "// call after any change that affects what should be shown\n\n"
        "The user attached mebane-weather-radar-widget.html (shown above) "
        "and asks: Any obvious improvements?"
    )
    # Bare scan of the full turn WOULD fire (that's the bug).
    assert session._parse_correction(body) is not None, (
        "precondition: file body still contains a correction-shaped phrase"
    )
    # Ask region alone must not.
    ask = session._persona_scan_region(body)
    assert session._parse_correction(ask) is None, f"ask must not correct: {ask!r}"

    class _Sess:
        pass

    s = session.ThreadSession.__new__(session.ThreadSession)
    s._pending_correction = None
    s.thread_id = "t"
    s._correction_count = 0
    s._memory_notices = []
    s.mcm = _FakeMCM([
        PersonaFact(text="The user is Stew Alexander, based in Mebane, NC.", kind="identity"),
        PersonaFact(
            text="The user attached index.html (shown above) and asks: summarize",
            kind="preference",
        ),
    ])
    # Bind real apply helpers used after a true hit (not exercised here).
    s._apply_correction = session.ThreadSession._apply_correction.__get__(s)
    handled = session.ThreadSession._handle_correction(s, body)
    assert handled is None, f"attach Q&A must not enter correction UI: {handled!r}"
    assert s._pending_correction is None
    print("ok: attach body does not trigger correction")


if __name__ == "__main__":
    test_parse_correction()
    test_match_and_apply()
    test_locator_noise()
    test_meta_directive_filter()
    test_polite_request_directives()
    test_attach_turn_does_not_promote_file_header()
    test_attach_pollution_helper()
    test_attach_body_does_not_trigger_correction()
    print("\nALL CORRECTION TESTS PASSED")
