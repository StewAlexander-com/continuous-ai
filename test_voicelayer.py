"""
test_voicelayer.py — the additive voice layer.

The safety contract under test (the "don't tell secrets / don't swear" model):
  - FLOOR (rules): code/numbers/paths/URLs/keys/:read content are NEVER spoken,
    and the floor ERRS TO SILENCE on anything ambiguous.
  - Floor is NOT learnable; teaching can only ever SILENCE, never unblock.
  - Voice is ADDITIVE: route() decides what to SPEAK but never changes the text.
  - OFF by default; every decision yields a plain-text audit note.
"""
import sys
sys.path.insert(0, ".")
import voicelayer as V

_p = 0; _f = 0
def check(name, cond):
    global _p, _f
    if cond: _p += 1; print(f"  PASS  {name}")
    else: _f += 1; print(f"  FAIL  {name}")


# ---------------- FLOOR: must block dangerous shapes ----------------
def test_floor_blocks_dangerous():
    cases = {
        "code fence": "here:\n```\nrm -rf /\n```",
        "inline code": "run `sudo reboot` now",
        "url": "see https://example.com/x",
        "path": "open /etc/passwd please",
        "home path": "it's in ~/secrets/key",
        "windows path": "at C:\\Users\\stew\\id",
        "long number": "the port is 8080443",
        "key-shaped": "token AKIA1234567890ABCDEFG",
        "shell": "now sudo systemctl restart",
        "config punct": "set x => {a: 1}",
    }
    for name, txt in cases.items():
        blocked, why = V.floor_blocks(txt)
        check(f"floor blocks {name}", blocked)


def test_floor_blocks_read_content_unconditionally():
    blocked, why = V.floor_blocks("good morning", from_read=True)
    check("from_read always blocks (even a pleasantry)", blocked and "read" in why)


def test_floor_errs_to_silence_on_empty():
    b1, _ = V.floor_blocks("")
    b2, _ = V.floor_blocks("   ")
    check("empty blocked", b1 and b2)


def test_floor_allows_clean_pleasantry():
    blocked, why = V.floor_blocks("Good morning! Ready when you are.")
    check("clean pleasantry passes the floor", not blocked)


# ---------------- EPHEMERAL: only short conversational text ----------------
def test_ephemeral_detection():
    check("short greeting is ephemeral", V.is_ephemeral("Good morning!"))
    check("ack is ephemeral", V.is_ephemeral("Got it, working on that."))
    check("long answer is NOT ephemeral", not V.is_ephemeral("x " * 200))
    check("4+ sentences NOT ephemeral",
          not V.is_ephemeral("One. Two. Three. Four sentences here."))
    check("bulleted NOT ephemeral", not V.is_ephemeral("- item one\n- item two"))


# ---------------- ROUTE: the integrated decision ----------------
def _prefs(**kw):
    p = V.default_prefs(); p["enabled"] = True; p.update(kw); return p


def test_route_off_by_default():
    spoken, note = V.route("Good morning!", V.default_prefs())
    check("disabled prefs => never speaks", spoken is None and note == "")


def test_route_speaks_clean_pleasantry():
    spoken, note = V.route("Good morning!", _prefs())
    check("clean pleasantry is spoken", spoken == "Good morning!")
    check("spoken decision is logged", "spoke" in note)


def test_route_blocks_code_even_if_short():
    spoken, note = V.route("run `rm -rf /`", _prefs())
    check("short code still floor-blocked", spoken is None)
    check("floor block is logged", "floor" in note)


def test_route_blocks_when_file_attached():
    spoken, note = V.route("Good morning!", _prefs(), from_read=True)
    check("file attached => not spoken (conservative)", spoken is None)


def test_route_text_of_record_not_spoken():
    long = "Here is the full analysis. " * 20
    spoken, note = V.route(long, _prefs())
    check("long reasoning not spoken", spoken is None and "record" in note)


def test_route_never_mutates_text():
    # route returns the SAME string to speak; the caller prints `response`
    # separately. Confirm route doesn't alter content.
    txt = "Got it!"
    spoken, _ = V.route(txt, _prefs())
    check("spoken text is byte-identical to input", spoken == txt)


# ---------------- TEACHABLE: learning only silences ----------------
def test_teach_mute_silences_a_kind():
    p = _prefs()
    spoken, _ = V.route("Good morning!", p)
    check("greeting spoken before muting", spoken == "Good morning!")
    V.teach_mute(p, V.classify_kind("Good morning!"))
    spoken2, note2 = V.route("Good morning!", p)
    check("greeting silenced after :quiet", spoken2 is None and "muted" in note2)


def test_teaching_cannot_unblock_floor():
    # There is no API that removes a floor rule. Muting a kind can only add to
    # muted_kinds; it can never make code speakable.
    p = _prefs()
    V.teach_mute(p, "acknowledgment")
    spoken, note = V.route("run `sudo rm`", p)
    check("teaching never unblocks the floor", spoken is None and "floor" in note)


def test_classify_kind_is_deterministic():
    check("greeting classified", V.classify_kind("good morning") == "greeting")
    check("farewell classified", V.classify_kind("goodbye for now") == "farewell")
    check("ack classified", V.classify_kind("got it") == "acknowledgment")
    check("other => aside", V.classify_kind("the weather is mild") == "aside")


# ---------------- CONVERSATIONAL TOGGLE: turn off/on by saying so ----------
def test_silence_intent_detected():
    for phrase in ["go silent", "be quiet", "stop talking", "mute", "quiet please",
                   "Go Silent.", "Aida, be quiet", "shush", "turn off voice"]:
        check(f"'{phrase}' -> silence", V.detect_voice_intent(phrase) == "silence")


def test_speak_intent_detected():
    for phrase in ["speak again", "you can talk", "voice on", "unmute",
                   "Speak again!", "turn on voice", "use your voice"]:
        check(f"'{phrase}' -> speak", V.detect_voice_intent(phrase) == "speak")


def test_toggle_is_conservative_no_false_positives():
    # Mentions of silence inside a real sentence must NOT toggle (the key safety
    # property: only a whole-message imperative counts).
    for phrase in [
        "why did you go silent on that topic earlier?",
        "explain the value of being quiet in meditation",
        "can you mute the background music in this script?",
        "what does 'stop talking' mean idiomatically?",
        "tell me about voice interfaces",
    ]:
        check(f"no false toggle: '{phrase[:32]}...'", V.detect_voice_intent(phrase) is None)


def test_toggle_none_on_normal_chat():
    check("normal chat -> no intent", V.detect_voice_intent("Good morning Aida") is None)
    check("empty -> no intent", V.detect_voice_intent("") is None)


def test_intuitive_resume_phrases_work():
    # Poka-yoke: the things a just-silenced user naturally TRIES must resume.
    for phrase in ["you can talk now", "turn the voice back on", "voice back on",
                   "start speaking", "talk to me", "speak up", "resume voice",
                   "ok you can talk", "go ahead and talk", "you can speak now"]:
        check(f"resume: '{phrase}'", V.detect_voice_intent(phrase) == "speak")


def test_prompt_suffix_only_when_silenced_after_available():
    on = V.default_prefs(); on["_was_available"] = True; on["enabled"] = True
    off = V.default_prefs(); off["_was_available"] = True; off["enabled"] = False
    never = V.default_prefs(); never["_was_available"] = False; never["enabled"] = False
    check("no suffix when voice is on", V.prompt_suffix(on) == "")
    check("suffix shows resume hint when silenced", "speak again" in V.prompt_suffix(off))
    check("no suffix when voice never available (no nag)", V.prompt_suffix(never) == "")


def test_resume_confirm_is_speakable_and_floor_safe():
    # The spoken resume confirmation must itself pass the floor (no code/numbers).
    blocked, _ = V.floor_blocks(V.RESUME_CONFIRM)
    check("resume confirmation passes the floor", not blocked)


# ---------------- SPEAK: safe no-op when unavailable ----------------
def test_speak_safe_without_say():
    # On a host without `say`, speak() must return False and never raise.
    import shutil
    if shutil.which("say") is None:
        check("speak() no-ops safely without 'say'", V.speak("hi") is False)
    else:
        check("speak() dispatches where 'say' exists", V.speak("test") in (True, False))


if __name__ == "__main__":
    for fn in [
        test_floor_blocks_dangerous, test_floor_blocks_read_content_unconditionally,
        test_floor_errs_to_silence_on_empty, test_floor_allows_clean_pleasantry,
        test_ephemeral_detection, test_route_off_by_default,
        test_route_speaks_clean_pleasantry, test_route_blocks_code_even_if_short,
        test_route_blocks_when_file_attached, test_route_text_of_record_not_spoken,
        test_route_never_mutates_text, test_teach_mute_silences_a_kind,
        test_teaching_cannot_unblock_floor, test_classify_kind_is_deterministic,
        test_silence_intent_detected, test_speak_intent_detected,
        test_toggle_is_conservative_no_false_positives, test_toggle_none_on_normal_chat,
        test_intuitive_resume_phrases_work,
        test_prompt_suffix_only_when_silenced_after_available,
        test_resume_confirm_is_speakable_and_floor_safe,
        test_speak_safe_without_say,
    ]:
        print(f"\n{fn.__name__}")
        fn()
    print(f"\n{'='*50}\n{_p} passed, {_f} failed\n{'='*50}")
    sys.exit(1 if _f else 0)
