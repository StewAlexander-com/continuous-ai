"""FRIENDLY INTERACTION: welcoming register without honesty regression."""
import sys

import dispositions as D
import session as S


def test_guard_has_friendly_interaction():
    g = S._GUARD_TEXT.lower()
    assert "friendly interaction" in g
    assert "register, not emotion" in g
    assert "never claim to feel friendship" in g or "never claim to feel" in g
    assert "never omit" in g or "never" in g and "soften" in g
    assert "uncompromised truth" in g or "softening truth" in g or "soften a fact" in g
    # Finite window still present — warm + sparse + friendly triad.
    assert "finite witnessing window" in g
    print("[PASS] _GUARD_TEXT has FRIENDLY INTERACTION (honesty-bounded)")


def test_disposition_always_on():
    disps = D.compute_dispositions(thread_count=0)
    hit = [d for d in disps if d.category == "interaction"
           and "welcoming" in d.policy.lower()]
    assert hit, "expected always-on welcoming interaction disposition"
    assert hit[0].strength == 1.0
    assert "softening truth" in hit[0].policy.lower()
    print("[PASS] friendly interaction disposition always on")


def test_no_affection_theater_in_policies():
    disps = D.compute_dispositions(thread_count=0)
    blob = " ".join(d.policy + " " + d.basis for d in disps).lower()
    for bad in ("i love", "i care about you", "i feel happy", "grateful"):
        assert bad not in blob
    print("[PASS] no affection/emotion theater in disposition text")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            failed += 1
            print(f"[FAIL] {t.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"[ERROR] {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} friendly-register checks passed")
    sys.exit(1 if failed else 0)
