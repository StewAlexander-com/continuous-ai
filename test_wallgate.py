"""Tests for wallgate.py — the cheap, model-free difficulty pre-gate that keeps
the collaborative wall high-fidelity (fires only on genuinely hard turns).

Run: ./.venv/bin/python test_wallgate.py
"""
import wallgate as wg


_passed = 0
_failed = 0


def check(cond, msg):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS  {msg}")
    else:
        _failed += 1
        print(f"  FAIL  {msg}")


def test_empty_turn_is_not_difficult():
    print("\ntest_empty_turn_is_not_difficult")
    a = wg.assess(wg.GateInputs())
    check(not a.should_deliberate, "no signals -> do not deliberate")
    check(a.difficulty == 0.0, "difficulty is 0 with no evidence")


def test_confident_substantive_turn_skips():
    print("\ntest_confident_substantive_turn_skips")
    # A long, confident, coherent answer to a plain ask: no difficulty markers,
    # high coherence, no caution, no corrections -> must NOT spend a deliberation.
    a = wg.assess(wg.GateInputs(
        caution_d=0.05,
        last_coherence=0.82,
        turns_since_correction=None,
        user_input="What time zone is Mebane in?",
        reply_text="Mebane, North Carolina is in the US Eastern time zone.",
    ))
    check(not a.should_deliberate, "confident coherent turn -> skip")


def test_caution_alone_can_trigger():
    print("\ntest_caution_alone_can_trigger")
    # The caution controller is the most-trusted signal: a high applied_d alone
    # (contested/low-confidence territory) is enough to warrant deliberation.
    a = wg.assess(wg.GateInputs(caution_d=0.9))
    check(a.caution_mu == 1.0, "caution_d 0.9 -> full membership")
    check(a.should_deliberate, "high caution alone clears the bar")


def test_low_coherence_alone_can_trigger():
    print("\ntest_low_coherence_alone_can_trigger")
    a = wg.assess(wg.GateInputs(last_coherence=0.20))
    check(a.low_coherence_mu == 1.0, "coherence 0.20 -> fully 'low'")
    check(a.should_deliberate, "low recent coherence alone clears the bar")


def test_single_heuristic_alone_is_not_enough():
    print("\ntest_single_heuristic_alone_is_not_enough")
    # One reply-tension marker, nothing else -> gameable heuristic must NOT alone
    # trigger an expensive deliberation.
    a = wg.assess(wg.GateInputs(reply_text="Well, it depends on your workload."))
    check(a.reply_markers >= 1, "detected a reply tension marker")
    check(not a.should_deliberate, "one weak heuristic alone -> skip")


def test_converging_heuristics_trigger():
    print("\ntest_converging_heuristics_trigger")
    # A genuine decision ask + a hedging reply: converging heuristics clear the bar
    # even without calibrated signals.
    a = wg.assess(wg.GateInputs(
        user_input="Should I use Redis or an in-memory cache — which is better, is it worth it?",
        reply_text="It depends; on the other hand there are real trade-offs either way.",
    ))
    check(a.ask_mu == 1.0, "multiple ask markers -> full ask membership")
    check(a.reply_mu == 1.0, "multiple reply markers -> full reply membership")
    check(a.should_deliberate, "converging difficulty heuristics clear the bar")


def test_recent_correction_raises_difficulty():
    print("\ntest_recent_correction_raises_difficulty")
    just = wg.assess(wg.GateInputs(turns_since_correction=0))
    old = wg.assess(wg.GateInputs(turns_since_correction=10))
    check(just.correction_mu == 1.0, "correction this turn -> fully recent")
    check(old.correction_mu == 0.0, "correction long ago -> decayed to 0")
    check(just.difficulty > old.difficulty, "recent correction raises difficulty")


def test_monotonic_evidence_only_raises():
    print("\ntest_monotonic_evidence_only_raises")
    # Noisy-OR invariant: adding any signal can only RAISE difficulty.
    base = wg.assess(wg.GateInputs(caution_d=0.4))
    more = wg.assess(wg.GateInputs(caution_d=0.4, last_coherence=0.3))
    check(more.difficulty >= base.difficulty - 1e-9, "adding evidence never lowers difficulty")


def test_deterministic():
    print("\ntest_deterministic")
    kw = dict(caution_d=0.5, last_coherence=0.4, turns_since_correction=2,
              user_input="which is better, X or Y?", reply_text="it depends, however...")
    a = wg.assess(wg.GateInputs(**kw))
    b = wg.assess(wg.GateInputs(**kw))
    check(a.to_log() == b.to_log(), "identical inputs -> identical assessment")


def test_cutoff_is_tunable():
    print("\ntest_cutoff_is_tunable")
    inp = wg.GateInputs(user_input="should I pick A or B?")  # single-ish ask signal
    strict = wg.assess(inp, cutoff=0.95)
    loose = wg.assess(inp, cutoff=0.10)
    check(not strict.should_deliberate, "strict cutoff suppresses a weak turn")
    check(loose.should_deliberate, "loose cutoff lets the same turn through")


def test_unknown_coherence_is_neutral_not_low():
    print("\ntest_unknown_coherence_is_neutral_not_low")
    a = wg.assess(wg.GateInputs(last_coherence=None))
    check(a.low_coherence_mu == 0.0, "unknown coherence contributes 0 (not treated as low)")


def test_audit_fields_present():
    print("\ntest_audit_fields_present")
    a = wg.assess(wg.GateInputs(caution_d=0.5, last_coherence=0.4))
    d = a.to_log()
    for k in ("caution_mu", "low_coherence_mu", "ask_mu", "reply_mu",
              "correction_mu", "contributions", "difficulty", "cutoff",
              "should_deliberate"):
        check(k in d, f"audit log has '{k}'")


if __name__ == "__main__":
    for fn in (
        test_empty_turn_is_not_difficult,
        test_confident_substantive_turn_skips,
        test_caution_alone_can_trigger,
        test_low_coherence_alone_can_trigger,
        test_single_heuristic_alone_is_not_enough,
        test_converging_heuristics_trigger,
        test_recent_correction_raises_difficulty,
        test_monotonic_evidence_only_raises,
        test_deterministic,
        test_cutoff_is_tunable,
        test_unknown_coherence_is_neutral_not_low,
        test_audit_fields_present,
    ):
        fn()
    print("\n" + "=" * 50)
    print(f"{_passed} passed, {_failed} failed")
    print("=" * 50)
    raise SystemExit(1 if _failed else 0)
