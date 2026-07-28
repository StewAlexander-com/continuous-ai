#!/usr/bin/env python3
"""Session wiring: caution inject is off by default; ON uses lagged critic only."""
import queue
import sys
import threading
import types

sys.path.insert(0, ".")
import session as S


def _shim_session(**kw):
    sess = S.ThreadSession.__new__(S.ThreadSession)
    sess.mcm = types.SimpleNamespace(current_state=lambda: None)
    sess.caution_controller_enabled = kw.get("caution_on", False)
    sess.speak_bias = False
    sess.caution_integral_half_life = 3.0
    sess.caution_wall_session_cap = 0.65
    sess.voice_enabled = False
    sess._messages = [{"role": "system", "content": "SYS"}]
    sess._critic_evals = kw.get("critic_evals", [])
    sess._critic_lock = threading.Lock()
    sess._correction_count = 0
    sess._turns_since_correction = kw.get("turns_since", None)
    sess._caution_applied_d = 0.0
    sess._caution_wall_fired = False
    sess._last_turn_substantive = kw.get("substantive", True)
    sess._history_window_turns = 24
    return sess


def test_off_by_default():
    sess = _shim_session()
    out = sess._caution_inject([{"role": "system", "content": "SYS"}])
    assert out[0]["content"] == "SYS"
    print("ok: caution off → no injection")


def test_on_with_low_critic_injects():
    ev = types.SimpleNamespace(coherence=0.22, is_infrastructure_noise=False)
    sess = _shim_session(caution_on=True, critic_evals=[(ev, "t")])
    out = sess._caution_inject([{"role": "system", "content": "SYS"}])
    assert "ASSERTION RESTRAINT" in out[0]["content"]
    assert out[0]["content"].startswith("SYS")
    print("ok: low lagged critic → restraint band injected")


def test_parse_error_evals_excluded_from_caution():
    """Neutral 0.5 from critic parse failures must not push restraint."""
    from schemas import CriticEvaluation
    noise = CriticEvaluation(
        coherence=0.5,
        notes="Critic parse error: response was not valid JSON",
    )
    assert noise.is_infrastructure_noise
    # Sustained parse failures alone → no restraint (empty usable scores).
    sess = _shim_session(caution_on=True, critic_evals=[(noise, "t")] * 5)
    out = sess._caution_inject([{"role": "system", "content": "SYS"}])
    assert "ASSERTION RESTRAINT" not in out[0]["content"], (
        "parse-error evals must not feed the caution buffer"
    )
    # Real low coherence still injects even if mixed with noise.
    real = types.SimpleNamespace(coherence=0.22, is_infrastructure_noise=False)
    sess2 = _shim_session(caution_on=True, critic_evals=[(noise, "t"), (real, "t")])
    out2 = sess2._caution_inject([{"role": "system", "content": "SYS"}])
    assert "ASSERTION RESTRAINT" in out2[0]["content"]
    print("ok: infrastructure-noise evals excluded from caution")


def test_model_window_off_unchanged():
    sess = _shim_session()
    sess._messages = [{"role": "system", "content": "SYS"},
                      {"role": "user", "content": "hi"}]
    w = sess._model_window()
    assert w[0]["content"] == "SYS"
    print("ok: _model_window unchanged when caution off")


if __name__ == "__main__":
    test_off_by_default()
    test_on_with_low_critic_injects()
    test_parse_error_evals_excluded_from_caution()
    test_model_window_off_unchanged()
    print("\nALL WIRING TESTS PASSED")
