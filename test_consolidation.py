"""
test_consolidation.py — L3 write-back (deltas -> cognitive_style + priors).

Covers the gates Stewart cares about:
  - NON-REGRESSIVE (absolute): EMA never deletes history; one bad delta can't
    wipe accumulated frameworks/weights.
  - HONESTY-GATED: quarantined + low-coherence deltas cannot reshape cognition.
  - BOUNDED: all fields stay within schema-asserted ranges.
  - DETERMINISTIC: same deltas -> same result.
  - FREQUENCY THRESHOLD: a single mention never crowns a 'dominant' framework.

Run: ./.venv/bin/python test_consolidation.py
"""
from __future__ import annotations

import sys

from schemas import CognitiveStyle, PersistentPriors, ThreadDelta
import consolidation as C


def _d(coh, sig=0.0, corr=0, fws=None, quar=False, emergent=False):
    return ThreadDelta(
        insight_gained="x",
        coherence_score=coh,
        user_correction_count=corr,
        weight_adjustment_signal=sig,
        frameworks_used=fws or [],
        quarantined=quar,
        emergent=emergent,
    )


_passed = 0
_failed = 0


def check(name, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failed += 1
        print(f"  FAIL  {name}")


def test_moves_off_defaults():
    s, p = CognitiveStyle(), PersistentPriors()
    deltas = [_d(0.9, sig=0.4, fws=["Second Arrow", "BLUF"]) for _ in range(5)]
    rep = C.consolidate_history(s, p, deltas)
    check("abstraction moved off 0.5", abs(s.abstraction_level - 0.5) > 1e-6)
    check("self_model_confidence moved off 0.5", abs(p.self_model_confidence - 0.5) > 1e-6)
    check("dominant_frameworks populated", set(s.dominant_frameworks) == {"Second Arrow", "BLUF"})
    check("report counts all used (none gated out)", rep.deltas_used == 5)


def test_honesty_gate_excludes_low_coherence_and_quarantine():
    s, p = CognitiveStyle(), PersistentPriors()
    deltas = [
        _d(0.2, fws=["JunkFramework"]),                 # below floor -> excluded
        _d(0.5, fws=["JunkFramework"]),                 # AT floor (<=) -> excluded
        _d(0.95, fws=["Confab"], quar=True),            # quarantined -> excluded
        _d(0.9, fws=["Gödel"]),                          # only this one counts
        _d(0.9, fws=["Gödel"]),
    ]
    rep = C.consolidate_history(s, p, deltas)
    check("gate keeps only 2 deltas", rep.deltas_used == 2)
    check("junk/quarantined frameworks NOT recorded as topic",
          "JunkFramework" not in p.topic_weights and "Confab" not in p.topic_weights)
    check("only gated framework can be dominant",
          s.dominant_frameworks == ["Gödel"])


def test_non_regressive_one_bad_delta_cannot_wipe():
    # Build up real history, snapshot, then feed one off-topic high-coherence delta.
    s, p = CognitiveStyle(), PersistentPriors()
    hist = [_d(0.9, sig=0.5, fws=["Second Arrow", "BLUF"]) for _ in range(8)]
    C.consolidate_history(s, p, hist)
    before_fw = set(p.topic_weights.keys())
    before_smc = p.self_model_confidence
    # one new, unrelated delta
    C.consolidate_one(s, p, _d(0.8, fws=["Kubernetes"]))
    check("prior frameworks survive a new unrelated delta",
          before_fw.issubset(set(p.topic_weights.keys())))
    check("self_model_confidence changes only incrementally (EMA, not clobbered)",
          abs(p.self_model_confidence - before_smc) < 0.2)


def test_bounds_respected_extremes():
    s, p = CognitiveStyle(), PersistentPriors()
    # hammer with max-positive then max-negative signals + many corrections
    deltas = [_d(1.0, sig=1.0, corr=9, fws=["A", "B", "C", "D"]) for _ in range(20)]
    deltas += [_d(1.0, sig=-1.0, corr=0, fws=["E"]) for _ in range(20)]
    C.consolidate_history(s, p, deltas)
    ok = (0.0 <= s.abstraction_level <= 1.0
          and 0.0 <= s.contradiction_tolerance <= 1.0
          and 0.0 <= p.trust_calibration <= 1.0
          and 0.0 <= p.self_model_confidence <= 1.0
          and all(0.0 <= v <= 1.0 for v in p.topic_weights.values()))
    check("all fields stay within [0,1] under extremes", ok)
    # __post_init__ asserts ranges; constructing from these must not raise
    try:
        CognitiveStyle(s.abstraction_level, s.dominant_frameworks,
                       s.contradiction_tolerance, s.uncertainty_expression)
        PersistentPriors(p.topic_weights, p.trust_calibration, p.self_model_confidence)
        check("resulting values pass schema __post_init__ asserts", True)
    except AssertionError:
        check("resulting values pass schema __post_init__ asserts", False)


def test_frequency_threshold_single_mention_not_dominant():
    s, p = CognitiveStyle(), PersistentPriors()
    deltas = [_d(0.9, fws=["Once"])]  # appears once
    deltas += [_d(0.9, fws=["Twice"]) for _ in range(2)]  # appears twice
    C.consolidate_history(s, p, deltas)
    check("single-mention framework excluded from dominant", "Once" not in s.dominant_frameworks)
    check("twice-mentioned framework included", "Twice" in s.dominant_frameworks)


def test_corrections_raise_trust_calibration():
    s, p = CognitiveStyle(), PersistentPriors()
    base = PersistentPriors().trust_calibration
    C.consolidate_history(s, p, [_d(0.9, corr=3) for _ in range(6)])
    check("user corrections raise trust_calibration above baseline",
          p.trust_calibration > base)


def test_uncertainty_flips_explicit_only_when_confident():
    # low coherence overall -> stays hedged
    s1, p1 = CognitiveStyle(), PersistentPriors()
    C.consolidate_history(s1, p1, [_d(0.55) for _ in range(3)])  # all at/below floor -> gated out
    check("no qualifying deltas -> stays hedged", s1.uncertainty_expression == "hedged")
    # high coherence -> becomes explicit
    s2, p2 = CognitiveStyle(), PersistentPriors()
    C.consolidate_history(s2, p2, [_d(0.95) for _ in range(10)])
    check("sustained high confidence -> explicit", s2.uncertainty_expression == "explicit")
    check("never suppressed", s2.uncertainty_expression != "suppressed")


def test_deterministic():
    deltas = [_d(0.8, sig=0.3, corr=1, fws=["Second Arrow", "BLUF"]) for _ in range(7)]
    s1, p1 = CognitiveStyle(), PersistentPriors()
    s2, p2 = CognitiveStyle(), PersistentPriors()
    C.consolidate_history(s1, p1, deltas)
    C.consolidate_history(s2, p2, deltas)
    check("same input -> same abstraction", abs(s1.abstraction_level - s2.abstraction_level) < 1e-12)
    check("same input -> same topic_weights", p1.topic_weights == p2.topic_weights)
    check("same input -> same frameworks", s1.dominant_frameworks == s2.dominant_frameworks)


def test_empty_history_is_noop():
    s, p = CognitiveStyle(), PersistentPriors()
    rep = C.consolidate_history(s, p, [])
    check("empty history leaves defaults untouched",
          s.abstraction_level == 0.5 and p.topic_weights == {} and rep.deltas_used == 0)


def test_report_renders():
    s, p = CognitiveStyle(), PersistentPriors()
    rep = C.consolidate_history(s, p, [_d(0.9, fws=["BLUF", "BLUF"]) for _ in range(3)])
    txt = rep.render()
    check("report renders with changed marker", "<-- changed" in txt and "CONSOLIDATION" in txt)


if __name__ == "__main__":
    for fn in [
        test_moves_off_defaults,
        test_honesty_gate_excludes_low_coherence_and_quarantine,
        test_non_regressive_one_bad_delta_cannot_wipe,
        test_bounds_respected_extremes,
        test_frequency_threshold_single_mention_not_dominant,
        test_corrections_raise_trust_calibration,
        test_uncertainty_flips_explicit_only_when_confident,
        test_deterministic,
        test_empty_history_is_noop,
        test_report_renders,
    ]:
        print(f"\n{fn.__name__}")
        fn()
    print(f"\n{'='*50}\n{_passed} passed, {_failed} failed\n{'='*50}")
    sys.exit(1 if _failed else 0)
