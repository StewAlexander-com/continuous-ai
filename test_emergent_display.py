"""[EMERGENT] is a runtime audit marker — strip from display, keep in storage."""
import sys

sys.path.insert(0, ".")
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


def test_strip_emergent_keeps_prose():
    raw = (
        "[EMERGENT] Rubber duck is usually for code.\n\n"
        "Pass 1: We the people declare independence."
    )
    shown = S.strip_emergent_markers_for_display(raw)
    check("tag gone", "[EMERGENT]" not in shown)
    check("aside kept", "usually for code" in shown)
    check("body kept", "Pass 1" in shown)
    print("[PASS] strip_emergent_markers_for_display")


def test_stream_filter_hides_tag():
    shown = []
    f = S._EmergentStreamFilter(lambda s: shown.append(s))
    f("[EMER")
    f("GENT] Usually for code. ")
    f("Pass 1 follows.")
    f.flush()
    out = "".join(shown)
    check("no tag in stream", "[EMERGENT]" not in out)
    check("prose streamed", "Usually for code" in out and "Pass 1" in out)
    print("[PASS] _EmergentStreamFilter hides tag")


def test_stored_text_still_extractable():
    raw = "[EMERGENT] Metaphor stretch noted.\n\nPass 1: Hello."
    detail = S.extract_emergent_detail(raw)
    check("extract still works", "Metaphor stretch" in detail)
    check("stops before pass", "Pass 1" not in detail)
    print("[PASS] extract_emergent_detail unchanged")


def test_start_prompt_softens_emergent():
    # The instruction lives in ThreadSession.start(); spot-check the shipped
    # phrasing by grepping the source string assembled in start via a shim.
    import inspect
    src = inspect.getsource(S.ThreadSession.start)
    check("natural prose instruction", "natural prose" in src)
    check("not wooden", "wooden" in src)
    check("still has marker", "[EMERGENT]" in src)
    print("[PASS] start() EMERGENT instruction is display-aware")


if __name__ == "__main__":
    test_strip_emergent_keeps_prose()
    test_stream_filter_hides_tag()
    test_stored_text_still_extractable()
    test_start_prompt_softens_emergent()
    print(f"\n{_p} passed, {_f} failed")
    sys.exit(1 if _f else 0)
