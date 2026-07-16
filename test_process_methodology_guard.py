"""USER-INVOKED PROCESS guard + caution carve-out (method vs fact)."""
import sys

sys.path.insert(0, ".")
import caution
import session as S

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


def test_guard_has_user_invoked_process():
    g = S._GUARD_TEXT.lower()
    check("section present", "user-invoked process" in g)
    check("rubber duck named", "rubber duck" in g or "rubber-duck" in g)
    check("method not fact", "method, not fact" in g)
    check("no refuse solely for metaphor", "do not refuse" in g and "metaphor" in g)
    check("confab boundary kept", "do not invent unseen files" in g or "do not invent" in g)
    check("fit aside allowed", "fit aside" in g and "one short" in g)
    check("no lecture", "do not lecture" in g)
    check("no deny-after", "closing denial" in g or "isn't really rubber" in g)
    check("passes may stay implicit", "presentation to judgment" in g)
    check("show passes only when useful", "materially improve clarity" in g)
    check("no empty pass labels", "never merely name pass categories" in g)
    check("final reflects full scope", "whole requested scope" in g)
    check("natural aside", "natural and conversational" in g or "not a titled section" in g)
    check("style not script", "style rather than memorizing a script" in g)
    check("wording may vary", "vary the wording naturally" in g)
    check("complete when asked", "complete substance" in g and "illustrative excerpt" in g)
    check("conclusion powers retained", all(
        phrase in g for phrase in (
            "wage war", "make peace", "form alliances", "conduct commerce",
            "lives, fortunes, and sacred honor",
        )
    ))
    check("plural states retained", "free and independent states (plural)" in g)
    check("united states correction", "never call that wording historically inaccurate" in g)
    check("no provenance claims", "training-data provenance" in g)
    check("no fake editions", "edition titles" in g or "archive citations" in g)
    print("[PASS] _GUARD_TEXT has USER-INVOKED PROCESS (honest aside, no lecture)")


def test_caution_bands_exclude_process_from_restraint():
    blob = " ".join(caution._BAND_PROMPTS[c].lower() for c in caution.CautionBand if c)
    check("guarded mentions process", "process" in blob and "rubber duck" in blob)
    check("restrained distinguishes", "not external facts" in blob or "process metaphors" in blob)
    check("decline-first not smuggled facts", "not smuggled facts" in blob or "presentation structure" in blob)
    print("[PASS] caution bands carve out user-invoked process")


def test_capability_guard_still_forbids_false_retrieval():
    g = S._GUARD_TEXT.lower()
    check("retrieval confab forbidden", "retrieval complete" in g or "never claim to have retrieved" in g)
    check("offline boundary", "offline" in g and "cannot" in g)
    print("[PASS] capability / retrieval honesty unchanged")


if __name__ == "__main__":
    test_guard_has_user_invoked_process()
    test_caution_bands_exclude_process_from_restraint()
    test_capability_guard_still_forbids_false_retrieval()
    print(f"\n{_p} passed, {_f} failed")
    sys.exit(1 if _f else 0)
