#!/usr/bin/env python3
"""Property test: applied disposition never sits below raw fuzzy output (floors
only raise), and band strength is monotonic in applied_d."""
import random
import sys

sys.path.insert(0, ".")
import caution as C


def test_applied_never_below_raw():
    rng = random.Random(42)
    for _ in range(500):
        n = rng.randint(0, 8)
        scores = [rng.uniform(0.0, 1.0) for _ in range(n)]
        turns = None if rng.random() < 0.4 else rng.randint(0, 10)
        prior = None if rng.random() < 0.5 else rng.uniform(0.0, 1.0)
        prev = rng.uniform(0.0, 1.0)
        inp = C.CautionInputs(
            coherence_scores=scores,
            turns_since_correction=turns,
            prior_last_coherence=prior,
            prev_applied_d=0.0,  # isolate raw vs floors in this test
            last_turn_substantive=rng.choice([True, False]),
        )
        rep = C.evaluate(inp, enabled=True)
        assert rep.applied_d >= rep.raw_d - 1e-9, (
            f"applied {rep.applied_d} < raw {rep.raw_d} for {inp}")
    print("ok: applied_d >= raw_d across randomized inputs")


def test_floors_only_raise():
    rng = random.Random(99)
    for _ in range(200):
        turns = rng.randint(0, C.CORRECTION_RECENCY_TURNS)
        inp = C.CautionInputs(turns_since_correction=turns, prev_applied_d=0.0)
        rep = C.evaluate(inp, enabled=True)
        floor = C._correction_crisp_floor(turns)
        assert rep.applied_d >= floor - 1e-9
    print("ok: correction crisp floor only raises disposition")


def test_band_monotonic_in_d():
    ds = [i / 100 for i in range(101)]
    bands = [C.band_strength(C.quantize_band(d)) for d in ds]
    for i in range(1, len(bands)):
        assert bands[i] >= bands[i - 1], f"non-monotonic at {ds[i]}"
    print("ok: quantize_band is monotonic in d")


def test_session_monotonic_prev():
    """prev_applied_d is a floor — disposition never drops below prior session step."""
    inp = C.CautionInputs(coherence_scores=[0.9], prev_applied_d=0.55)
    rep = C.evaluate(inp, enabled=True)
    assert rep.applied_d >= 0.55 - 1e-9
    print("ok: prev_applied_d floor holds")


if __name__ == "__main__":
    test_applied_never_below_raw()
    test_floors_only_raise()
    test_band_monotonic_in_d()
    test_session_monotonic_prev()
    print("\nALL DOWNWARD-ONLY TESTS PASSED")
