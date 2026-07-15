"""Tests for dispositions.py — structural preferences (no model, no DB)."""
import sys
sys.path.insert(0, ".")
import dispositions as D
from schemas import CognitiveStyle, PersistentPriors

_p = 0
_f = 0


def check(name, cond):
    global _p, _f
    if cond:
        _p += 1
        print(f"  PASS  {name}")
    else:
        _f += 1
        print(f"  FAIL  {name}")


def test_fresh_session_has_integrity_dispositions():
    disps = D.compute_dispositions(thread_count=0)
    cats = {d.category for d in disps}
    check("integrity dispositions always present", "integrity" in cats)
    check("uncertainty preference present",
          any("uncertainty" in d.policy.lower() for d in disps))
    check("finite witnessing window disposition present",
          any("finite attention" in d.policy.lower()
              and "meaning-making" in d.policy.lower() for d in disps))
    texts = " ".join(d.policy for d in disps).lower()
    check("no emotional language in policies",
          "feel" not in texts and "grateful" not in texts
          and "like chocolate" not in texts)


def test_l3_frameworks_surface():
    style = CognitiveStyle(dominant_frameworks=["Second Arrow", "Bayesian updating"])
    disps = D.compute_dispositions(cognitive_style=style, thread_count=3)
    check("framework preference listed",
          any("Second Arrow" in d.policy for d in disps))


def test_trust_calibration_disposition():
    priors = PersistentPriors(trust_calibration=0.75)
    disps = D.compute_dispositions(persistent_priors=priors, thread_count=2)
    check("high trust -> defer to user corrections",
          any("defer" in d.policy.lower() for d in disps))


def test_speak_bias_disposition():
    disps = D.compute_dispositions(speak_bias=True, thread_count=0)
    check("speak_bias adds interaction disposition",
          any("voicing" in d.policy.lower() or "voice" in d.policy.lower() for d in disps))


def test_render_block_includes_pedagogy():
    block = D.render_dispositions_block(D.compute_dispositions())
    check("pedagogy mentions policy not emotion",
          "policy" in block.lower() and "not emotion" in block.lower())
    check("active dispositions header", "ACTIVE DISPOSITIONS" in block)


def test_status_renderer_groups_by_category():
    status = D.render_dispositions_status(D.compute_dispositions(speak_bias=True))
    check("status mentions structural", "structural" in status.lower())
    check("status has integrity section", "integrity:" in status)


if __name__ == "__main__":
    for fn in [
        test_fresh_session_has_integrity_dispositions,
        test_l3_frameworks_surface,
        test_trust_calibration_disposition,
        test_speak_bias_disposition,
        test_render_block_includes_pedagogy,
        test_status_renderer_groups_by_category,
    ]:
        print(f"\n{fn.__name__}")
        fn()
    print(f"\n{'='*50}\n{_p} passed, {_f} failed\n{'='*50}")
    sys.exit(1 if _f else 0)
