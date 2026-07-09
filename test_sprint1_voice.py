"""Sprint 1 voice dynamics: verbosity, caution-aware suppression, overlap dispatch."""
import sys
sys.path.insert(0, ".")
import voicelayer as V

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


def _prefs(**kw):
    p = V.default_prefs()
    p["enabled"] = True
    p.update(kw)
    return p


def test_default_verbosity_unchanged():
    long = "Lead sentence here. " + ("More detail. " * 40)
    a, _ = V.route(long, _prefs(), speak_bias=True, lead_sentences=1)
    b, _ = V.route(long, _prefs(verbosity="normal"), speak_bias=True, lead_sentences=1)
    check("normal verbosity matches legacy lead speak", a == b == "Lead sentence here.")
    check("default prefs include verbosity normal", V.default_prefs()["verbosity"] == "normal")


def test_chatty_two_lead_sentences():
    long = "First part. Second part. " + ("Tail. " * 30)
    spoken, note = V.route(long, _prefs(verbosity="chatty"), speak_bias=True, lead_sentences=1)
    check("chatty speaks two leads", spoken == "First part. Second part.")
    check("audit marks lead", "spoke lead" in note)


def test_terse_no_lead_on_long_reply():
    long = "A lead. " + ("More. " * 30)
    spoken, note = V.route(long, _prefs(verbosity="terse"), speak_bias=True, lead_sentences=1)
    check("terse skips lead on long reply", spoken is None and "record" in note)


def test_terse_still_speaks_short_ephemeral():
    spoken, note = V.route("Got it!", _prefs(verbosity="terse"), speak_bias=True)
    check("terse still speaks short ack", spoken == "Got it!")


def test_caution_restrained_suppresses():
    long = "Safe lead. " + ("x. " * 30)
    spoken, note = V.route(long, _prefs(), speak_bias=True, caution_band=2)
    check("RESTRAINED suppresses speak", spoken is None)
    check("caution noted", "caution" in note and "RESTRAINED" in note)


def test_caution_chatty_override():
    long = "Override lead. Second bit. " + ("x. " * 30)
    spoken, note = V.route(
        long, _prefs(verbosity="chatty"), speak_bias=True, caution_band=3
    )
    check("chatty overrides caution suppression", spoken == "Override lead. Second bit.")
    check("not suppressed note", "suppressed" not in note)


def test_prewarm_no_raise_without_model():
    try:
        V.prewarm_kokoro("/nonexistent/model.onnx", "/nonexistent/voices.bin")
        check("prewarm no-op safe without model", True)
    except Exception as e:
        check(f"prewarm must not raise ({e})", False)


if __name__ == "__main__":
    for fn in [
        test_default_verbosity_unchanged,
        test_chatty_two_lead_sentences,
        test_terse_no_lead_on_long_reply,
        test_terse_still_speaks_short_ephemeral,
        test_caution_restrained_suppresses,
        test_caution_chatty_override,
        test_prewarm_no_raise_without_model,
    ]:
        print(f"\n{fn.__name__}")
        fn()
    print(f"\n{'='*50}\n{_p} passed, {_f} failed\n{'='*50}")
    sys.exit(1 if _f else 0)
