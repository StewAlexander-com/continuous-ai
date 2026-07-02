#!/usr/bin/env python3
"""Same inputs → identical disposition + identical firing log."""
import sys

sys.path.insert(0, ".")
import caution as C


def _sample_inputs():
    return C.CautionInputs(
        coherence_scores=[0.55, 0.48, 0.41],
        turns_since_correction=2,
        delib_coherence=0.38,
        delib_thesis=0.52,
        delib_antithesis=0.50,
        prior_last_coherence=0.44,
        last_turn_substantive=True,
        prev_applied_d=0.25,
    )


def test_deterministic_evaluate():
    inp = _sample_inputs()
    a = C.evaluate(inp, enabled=True)
    b = C.evaluate(inp, enabled=True)
    assert a.to_log() == b.to_log()
    assert a.render() == b.render()
    assert a.band == b.band
    print("ok: identical inputs → identical report")


def test_deterministic_prompt():
    inp = _sample_inputs()
    band = C.evaluate(inp, enabled=True).band
    assert C.prompt_line(band) == C.prompt_line(band)
    print("ok: prompt_line deterministic")


if __name__ == "__main__":
    test_deterministic_evaluate()
    test_deterministic_prompt()
    print("\nALL DETERMINISM TESTS PASSED")
