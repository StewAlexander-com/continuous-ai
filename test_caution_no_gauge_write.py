#!/usr/bin/env python3
"""Assert the caution controller performs no upward write to self_model_confidence."""
import sys
import types

sys.path.insert(0, ".")
import caution as C
from schemas import PersistentPriors, CognitiveStyle


def test_evaluate_is_pure():
    priors = PersistentPriors(self_model_confidence=0.5)
    before = priors.self_model_confidence
    inp = C.CautionInputs(
        coherence_scores=[0.2, 0.15],
        turns_since_correction=0,
        prior_last_coherence=0.3,
        prev_applied_d=0.0,
    )
    rep = C.evaluate(inp, enabled=True)
    _ = C.prompt_line(rep.band)
    _ = C.apply_disposition_to_prompt("SYS", rep)
    assert priors.self_model_confidence == before
    print("ok: evaluate/prompt_line touch no PersistentPriors")


def test_session_inject_no_gauge_write():
    """ThreadSession._caution_inject must not mutate MCM priors."""
    import queue
    import threading
    import session as S

    class _FakeMCM:
        def __init__(self):
            from schemas import ContextState, CognitiveStyle, PersistentPriors
            self._state = types.SimpleNamespace(
                persistent_priors=PersistentPriors(self_model_confidence=0.48),
                cognitive_style=CognitiveStyle(),
            )
        def current_state(self):
            return types.SimpleNamespace(
                thread_deltas=[types.SimpleNamespace(coherence_score=0.42)],
            )

    sess = S.ThreadSession.__new__(S.ThreadSession)
    sess.mcm = _FakeMCM()
    sess.caution_controller_enabled = True
    sess.speak_bias = False
    sess._critic_evals = []
    sess._critic_lock = threading.Lock()
    sess._correction_count = 0
    sess._turns_since_correction = None
    sess._caution_applied_d = 0.0
    sess._caution_wall_fired = False
    sess._last_delib_critic = None
    sess._last_turn_substantive = False
    sess._messages = [{"role": "system", "content": "BASE"}]
    before = sess.mcm._state.persistent_priors.self_model_confidence
    out = sess._caution_inject([{"role": "system", "content": "BASE"}])
    after = sess.mcm._state.persistent_priors.self_model_confidence
    assert before == after == 0.48
    assert out[0]["content"].startswith("BASE")
    print("ok: _caution_inject does not write self_model_confidence")


if __name__ == "__main__":
    test_evaluate_is_pure()
    test_session_inject_no_gauge_write()
    print("\nALL NO-GAUGE-WRITE TESTS PASSED")
