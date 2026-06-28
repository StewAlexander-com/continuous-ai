"""
test_kokoro_voice.py — the local neural (Kokoro) speech backend in voicelayer.

What's under test (engine dispatch ONLY; the floor/eligibility/mute gates are
covered by test_voicelayer.py and are NOT re-touched here):
  - kokoro_available() is False, cleanly, when deps OR model files are absent.
  - speak(engine="kokoro") routes to Kokoro when available, and FALLS BACK to
    `say` when Kokoro is unavailable.
  - speak() returns False (never raises) when NO engine can dispatch.
  - Real model is never required: kokoro_onnx + soundfile + afplay are mocked.

All mocking is local + reversible (sys.modules + monkeypatched module attrs),
restored in a finally so test order can't leak fakes between cases.
"""
import os
import sys
import types
import tempfile

sys.path.insert(0, ".")
import voicelayer as V

_p = 0; _f = 0
def check(name, cond):
    global _p, _f
    if cond: _p += 1; print(f"  PASS  {name}")
    else: _f += 1; print(f"  FAIL  {name}")


# ---------------------------------------------------------------------------
# Helpers: install/remove fake kokoro_onnx + soundfile in sys.modules so the
# in-function `import` inside voicelayer picks them up, and reset the module's
# lazy singleton between cases so a cached model/failure never leaks.
# ---------------------------------------------------------------------------
class _FakeKokoro:
    def __init__(self, model_path, voices_path):
        self.model_path = model_path
        self.voices_path = voices_path
    def create(self, text, voice="af_kore", speed=1.0, lang="en-us"):
        # Return tiny dummy (samples, rate) — soundfile.write is also faked.
        return ([0.0, 0.0, 0.0], 24000)


def _install_fake_kokoro_deps():
    saved = {k: sys.modules.get(k) for k in ("kokoro_onnx", "soundfile")}
    ko = types.ModuleType("kokoro_onnx")
    ko.Kokoro = _FakeKokoro
    sf = types.ModuleType("soundfile")
    sf.write = lambda path, samples, rate: open(path, "wb").close()
    sys.modules["kokoro_onnx"] = ko
    sys.modules["soundfile"] = sf
    return saved


def _remove_kokoro_deps():
    """Force deps to be UN-importable (simulate not installed)."""
    saved = {k: sys.modules.get(k) for k in ("kokoro_onnx", "soundfile")}
    # Sentinel that raises on import via a meta-path hook is overkill; instead
    # set them to None, which makes `import x` raise ImportError.
    sys.modules["kokoro_onnx"] = None
    sys.modules["soundfile"] = None
    return saved


def _restore(saved):
    for k, v in saved.items():
        if v is None:
            sys.modules.pop(k, None)
        else:
            sys.modules[k] = v


def _reset_singleton():
    V._KOKORO_MODEL = None
    V._KOKORO_KEY = None
    V._KOKORO_LOAD_FAILED = False


# ---------------------------------------------------------------------------
# kokoro_available()
# ---------------------------------------------------------------------------
def test_unavailable_when_deps_absent():
    saved = _remove_kokoro_deps()
    try:
        _reset_singleton()
        # Even if files existed, missing deps => unavailable.
        check("kokoro_available False when deps missing",
              V.kokoro_available("nope.onnx", "nope.bin") is False)
    finally:
        _restore(saved)


def test_unavailable_when_model_files_absent():
    saved = _install_fake_kokoro_deps()
    try:
        _reset_singleton()
        check("kokoro_available False when model files missing",
              V.kokoro_available("/no/such/model.onnx", "/no/such/voices.bin") is False)
    finally:
        _restore(saved)


def test_available_when_deps_and_files_present():
    saved = _install_fake_kokoro_deps()
    f1 = tempfile.NamedTemporaryFile(suffix=".onnx", delete=False)
    f2 = tempfile.NamedTemporaryFile(suffix=".bin", delete=False)
    f1.close(); f2.close()
    try:
        _reset_singleton()
        check("kokoro_available True when deps + files present",
              V.kokoro_available(f1.name, f2.name) is True)
    finally:
        _restore(saved)
        os.remove(f1.name); os.remove(f2.name)


# ---------------------------------------------------------------------------
# speak() dispatch + fallback
# ---------------------------------------------------------------------------
def test_speak_routes_to_kokoro_when_available():
    saved = _install_fake_kokoro_deps()
    f1 = tempfile.NamedTemporaryFile(suffix=".onnx", delete=False)
    f2 = tempfile.NamedTemporaryFile(suffix=".bin", delete=False)
    f1.close(); f2.close()
    calls = {"kokoro": 0, "say": 0, "play": []}
    orig_play, orig_say = V._play_and_cleanup, V._speak_say
    def fake_play(wav_path):
        calls["play"].append(wav_path)
        try: os.remove(wav_path)
        except Exception: pass
        return True
    def fake_say(text, *, voice=None):
        calls["say"] += 1
        return True
    try:
        _reset_singleton()
        V._play_and_cleanup = fake_play
        V._speak_say = fake_say
        ok = V.speak("Good morning!", voice="af_kore", engine="kokoro",
                     model_path=f1.name, voices_path=f2.name)
        check("speak(engine=kokoro) dispatched", ok is True)
        check("kokoro path produced a wav to play", len(calls["play"]) == 1)
        check("say fallback NOT used when kokoro works", calls["say"] == 0)
    finally:
        V._play_and_cleanup, V._speak_say = orig_play, orig_say
        _restore(saved)
        os.remove(f1.name); os.remove(f2.name)


def test_speak_falls_back_to_say_when_kokoro_unavailable():
    saved = _remove_kokoro_deps()      # kokoro deps missing => unavailable
    calls = {"say": 0}
    orig_say = V._speak_say
    def fake_say(text, *, voice=None):
        calls["say"] += 1
        return True
    try:
        _reset_singleton()
        V._speak_say = fake_say
        ok = V.speak("Good morning!", voice="af_kore", engine="kokoro",
                     model_path="nope.onnx", voices_path="nope.bin")
        check("speak falls back to say when kokoro unavailable", ok is True)
        check("say fallback was used exactly once", calls["say"] == 1)
    finally:
        V._speak_say = orig_say
        _restore(saved)


def test_speak_returns_false_when_no_engine():
    saved = _remove_kokoro_deps()
    orig_say_avail = V.say_available
    try:
        _reset_singleton()
        V.say_available = lambda: False     # neither kokoro nor say
        ok = V.speak("Good morning!", engine="kokoro",
                     model_path="nope.onnx", voices_path="nope.bin")
        check("speak returns False when no engine available", ok is False)
    finally:
        V.say_available = orig_say_avail
        _restore(saved)


def test_speak_never_raises_on_synth_error():
    # Kokoro "available" but create() blows up => must fall back to say, no raise.
    saved = _install_fake_kokoro_deps()
    f1 = tempfile.NamedTemporaryFile(suffix=".onnx", delete=False)
    f2 = tempfile.NamedTemporaryFile(suffix=".bin", delete=False)
    f1.close(); f2.close()
    class _Boom(_FakeKokoro):
        def create(self, *a, **k):
            raise RuntimeError("synth boom")
    sys.modules["kokoro_onnx"].Kokoro = _Boom
    calls = {"say": 0}
    orig_say = V._speak_say
    def fake_say(text, *, voice=None):
        calls["say"] += 1
        return True
    try:
        _reset_singleton()
        V._speak_say = fake_say
        ok = V.speak("Good morning!", engine="kokoro",
                     model_path=f1.name, voices_path=f2.name)
        check("synth error => fell back to say without raising", ok is True)
        check("say used once after synth error", calls["say"] == 1)
    finally:
        V._speak_say = orig_say
        _restore(saved)
        os.remove(f1.name); os.remove(f2.name)


def test_voice_available_kokoro_or_say():
    # engine=kokoro is available if EITHER kokoro or say is.
    saved = _remove_kokoro_deps()
    orig_say_avail = V.say_available
    try:
        _reset_singleton()
        V.say_available = lambda: True
        check("voice_available(kokoro) True via say fallback",
              V.voice_available("kokoro", "nope.onnx", "nope.bin") is True)
        V.say_available = lambda: False
        check("voice_available(kokoro) False when neither present",
              V.voice_available("kokoro", "nope.onnx", "nope.bin") is False)
    finally:
        V.say_available = orig_say_avail
        _restore(saved)


def test_empty_text_never_dispatches():
    check("speak('') is False", V.speak("", engine="kokoro") is False)
    check("speak('   ') is False", V.speak("   ", engine="kokoro") is False)


if __name__ == "__main__":
    for fn in [
        test_unavailable_when_deps_absent,
        test_unavailable_when_model_files_absent,
        test_available_when_deps_and_files_present,
        test_speak_routes_to_kokoro_when_available,
        test_speak_falls_back_to_say_when_kokoro_unavailable,
        test_speak_returns_false_when_no_engine,
        test_speak_never_raises_on_synth_error,
        test_voice_available_kokoro_or_say,
        test_empty_text_never_dispatches,
    ]:
        print(f"\n{fn.__name__}")
        fn()
    print(f"\n{'='*50}\n{_p} passed, {_f} failed\n{'='*50}")
    sys.exit(1 if _f else 0)
