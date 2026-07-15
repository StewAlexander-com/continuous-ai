"""Context-aware speaking: prefer voice; soft-floor recover; light turns speak."""
import sys

import voicelayer as V


def _prefs(**kw):
    p = V.default_prefs()
    p["enabled"] = True
    p.update(kw)
    return p


def test_soft_floor_meta_still_speaks_greeting_lead():
    # The Hi Aida failure mode: clean greeting + meta footnote with '=' / scores.
    reply = (
        "Hi Stew — ready when you are today.  \n\n"
        "*(BLUF: Short, action-oriented, and aligned with your top priority "
        "(BLUF=0.69) and recent threads.)*"
    )
    blocked, why = V.floor_blocks(reply)
    assert blocked and why in V._SOFT_FLOOR_REASONS
    spoken, note = V.route(reply, _prefs(), speak_bias=True, turn_weight="light")
    assert spoken is not None
    assert spoken.startswith("Hi Stew")
    assert "BLUF=0.69" not in spoken
    assert "soft-floor recover" in note or "spoke" in note
    assert spoken in reply
    print("[PASS] soft-floor meta: speaks clean greeting lead")


def test_hard_floor_code_fence_still_silent():
    txt = "intro line.\n```\nrm -rf /\n```\nmore."
    spoken, note = V.route(txt, _prefs(), speak_bias=True, turn_weight="light")
    assert spoken is None and "floor" in note
    print("[PASS] hard floor (code fence) still silences whole reply")


def test_from_read_still_blocks():
    long = "Safe lead sentence. " + ("more. " * 30)
    spoken, note = V.route(
        long, _prefs(), speak_bias=True, from_read=True, turn_weight="light",
    )
    assert spoken is None and "floor" in note
    print("[PASS] :read still blocks even on light turns")


def test_light_turn_forces_speak_preference():
    # Long-ish but has a clean first sentence; speak_bias OFF — light still speaks.
    long = "Ready when you are. " + ("Then a lot more detail here. " * 25)
    silent, note0 = V.route(long, _prefs(), speak_bias=False, turn_weight="standard")
    assert silent is None and "record" in note0
    spoken, note = V.route(long, _prefs(), speak_bias=False, turn_weight="light")
    assert spoken == "Ready when you are."
    assert "spoke" in note
    print("[PASS] light turn prefers speaking even without speak_bias")


def test_caution_does_not_mute_light_greetings():
    spoken, note = V.route(
        "Hi Stew! Ready when you are.",
        _prefs(), speak_bias=True,
        turn_weight="light", caution_band=2,
    )
    assert spoken is not None and "Hi Stew" in spoken
    assert "caution" not in note
    print("[PASS] light greeting speaks under caution RESTRAINED")


def test_caution_still_suppresses_substantive():
    long = "Here is the short answer up front. " + ("Then detail. " * 30)
    spoken, note = V.route(
        long, _prefs(), speak_bias=True,
        turn_weight="standard", caution_band=2,
    )
    assert spoken is None and "caution" in note and "RESTRAINED" in note
    print("[PASS] caution RESTRAINED still suppresses substantive speech")


def test_spoken_invariant_holds():
    batch = [
        "Hi Stew — ready today.  \n\n*(BLUF=0.69 score)*",
        "Good morning!",
        "Here is the answer. " + ("Detail. " * 40),
        "intro line.\n```\ncode\n```\n",
    ]
    for txt in batch:
        for tw in ("light", "standard"):
            spoken, _ = V.route(txt, _prefs(), speak_bias=True, turn_weight=tw)
            if spoken is not None:
                assert spoken in txt
                blocked, _ = V.floor_blocks(spoken)
                assert not blocked
    print("[PASS] spoken ⊆ printed and floor-clean under both turn weights")


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
    print(f"\n{len(tests) - failed}/{len(tests)} speak-context checks passed")
    sys.exit(1 if failed else 0)
