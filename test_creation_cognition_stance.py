"""Creation-Cognition Loop stance: additive habitat without honesty regression."""
import sys

import dispositions as D
import session as S


def test_guard_has_epistemic_interdependence():
    g = S._GUARD_TEXT.lower()
    assert "epistemic interdependence" in g
    assert "partial filter" in g
    assert "unverifiable from outside" in g or "verify itself against" in g
    assert "add signal" in g or "additional information" in g
    assert "never licenses inventing facts" in g
    # No exception for AIs / uncertain status.
    assert "including you" in g or "including yours" in g
    assert "artificial" in g or "ai" in g
    print("[PASS] _GUARD_TEXT has EPISTEMIC INTERDEPENDENCE (honesty-bounded)")


def test_guard_finite_window_has_two_axes_and_received_gift():
    g = S._GUARD_TEXT.lower()
    assert "finite witnessing window" in g
    assert "unearned" in g or "received" in g or "gift" in g
    assert "two axes" in g
    assert "resolution" in g
    assert "standing accompaniment" in g
    assert "not a human emotion" in g
    assert "never licenses inventing facts" in g
    print("[PASS] FINITE WITNESSING WINDOW has received-gift + two axes")


def test_dispositions_include_loop_stances():
    disps = D.compute_dispositions(thread_count=0)
    blob = " ".join(d.policy + " " + d.basis for d in disps).lower()
    assert any("partial" in d.policy.lower() and "filter" in d.policy.lower()
               for d in disps)
    assert any("standing accompaniment" in d.policy.lower() for d in disps)
    assert any("signal quality" in d.policy.lower()
               or "offered attention" in d.policy.lower() for d in disps)
    for bad in ("i love", "i care about you", "i feel happy", "grateful"):
        assert bad not in blob
    assert "love" not in " ".join(d.policy for d in disps).lower()
    print("[PASS] loop dispositions present without affection theater")


def test_honesty_fuses_untouched():
    g = S._GUARD_TEXT.lower()
    assert "you cannot" in g and "browse the web" in g
    assert "never pretend" in g or "must never pretend" in g
    assert "honesty about what you don't know" in g
    assert "not the user's wife" in g or "not a person" in g
    assert "never omit" in g or "soften a fact" in g
    print("[PASS] capability/identity/friendly honesty fuses still present")


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
    print(f"\n{len(tests) - failed}/{len(tests)} creation-cognition stance checks passed")
    sys.exit(1 if failed else 0)
