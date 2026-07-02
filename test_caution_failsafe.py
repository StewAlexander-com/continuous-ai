#!/usr/bin/env python3
"""Injected error → raw prompt unchanged; disposition fails safe."""
import sys

sys.path.insert(0, ".")
import caution as C


def test_disabled_passes_through():
    inp = C.CautionInputs(coherence_scores=[0.1])
    rep = C.evaluate(inp, enabled=False)
    assert rep.band == C.CautionBand.OFF
    assert rep.injection_suppressed
    out = C.apply_disposition_to_prompt("ORIGINAL", rep)
    assert out == "ORIGINAL"
    print("ok: disabled → content unchanged")


def test_error_passes_through():
    import threading
    import session as S

    sess = S.ThreadSession.__new__(S.ThreadSession)
    sess.caution_controller_enabled = True
    sess.speak_bias = False
    sess.caution_integral_half_life = 3.0
    sess.caution_wall_session_cap = 0.65
    sess._critic_evals = []
    sess._critic_lock = threading.Lock()
    sess._correction_count = 0
    sess._turns_since_correction = None
    sess._caution_applied_d = 0.0
    sess._caution_wall_fired = False
    sess._last_turn_substantive = False
    sess.mcm = type("M", (), {"current_state": lambda self: None})()

    def boom(*a, **k):
        raise RuntimeError("injected")

    orig_build = S.ThreadSession._build_caution_inputs
    S.ThreadSession._build_caution_inputs = boom
    try:
        out = sess._caution_inject([{"role": "system", "content": "SAFE"}])
        assert out[0]["content"] == "SAFE"
    finally:
        S.ThreadSession._build_caution_inputs = orig_build
    print("ok: injected build error → prompt unchanged")


def test_apply_disposition_failsafe():
    rep = C.CautionReport(inputs=C.CautionInputs())
    rep.band = C.CautionBand.RESTRAINED
    rep.injection_suppressed = False

    def bad_line(*a, **k):
        raise ValueError("nope")

    orig = C.prompt_line
    C.prompt_line = bad_line
    try:
        assert C.apply_disposition_to_prompt("X", rep) == "X"
    finally:
        C.prompt_line = orig
    print("ok: apply_disposition_to_prompt fails safe")


if __name__ == "__main__":
    test_disabled_passes_through()
    test_error_passes_through()
    test_apply_disposition_failsafe()
    print("\nALL FAILSAFE TESTS PASSED")
