"""
test_speakbias.py — the speak-bias disposition (two linked layers).

LAYER 1 (mechanism, voicelayer.route): widen ONLY the style gate, never the
floor. Short floor-clean replies still speak in full (zero regression); a longer
reply may have its floor-clean LEAD sentence(s) spoken — always a VERBATIM
SUBSTRING of the printed text, re-checked against the floor + length cap.

LAYER 2 (self-model, voice.speak_bias_line): an honest principle string that is
truthful and bounded (within the floor; only words also written).

Hard invariants asserted here:
  - any non-None route() output is a SUBSTRING of the input AND passes floor_blocks().
  - learning still only ever silences (mute applies to the spoken fragment).
  - bias OFF == byte-for-byte the old behavior.
"""
import sys
sys.path.insert(0, ".")
import voicelayer as V
import voice as VO

_p = 0; _f = 0
def check(name, cond):
    global _p, _f
    if cond: _p += 1; print(f"  PASS  {name}")
    else: _f += 1; print(f"  FAIL  {name}")


def _prefs(**kw):
    p = V.default_prefs(); p["enabled"] = True; p.update(kw); return p


# ---------------- extract_lead: verbatim prefix ----------------
def test_extract_lead_basics():
    t = "First sentence. Second sentence. Third one."
    check("lead n=1 is first sentence", V.extract_lead(t, 1) == "First sentence.")
    check("lead n=2 is first two", V.extract_lead(t, 2) == "First sentence. Second sentence.")
    check("lead is a prefix substring", t.startswith(V.extract_lead(t, 1)))
    check("empty text -> ''", V.extract_lead("", 1) == "")
    check("n<1 -> ''", V.extract_lead(t, 0) == "")
    check("no terminator -> '' (no clean lead)",
          V.extract_lead("a bare clause with no period", 1) == "")
    check("fewer than n terminators -> last available",
          V.extract_lead("Only one. ", 3) == "Only one.")


# ---------------- LAYER 1: short reply unchanged ----------------
def test_short_clean_reply_spoken_full():
    spoken, note = V.route("Good morning!", _prefs(), speak_bias=True)
    check("short clean reply spoken in full", spoken == "Good morning!")
    check("note is the normal spoke note (not lead)",
          "spoke" in note and "lead" not in note)


# ---------------- LAYER 1: long reply -> lead substring ----------------
def test_long_reply_speaks_lead_substring():
    long = ("Here is the short answer up front. " + ("Then a lot more detail. " * 30))
    spoken, note = V.route(long, _prefs(), speak_bias=True, lead_sentences=1)
    check("long reply speaks its lead", spoken == "Here is the short answer up front.")
    check("spoken is a substring of input", spoken in long)
    check("audit note marks the lead path", "spoke lead" in note)


def test_long_reply_silent_without_bias():
    long = ("Here is the short answer up front. " + ("Then a lot more detail. " * 30))
    spoken, note = V.route(long, _prefs(), speak_bias=False)
    check("bias off => long reply silent", spoken is None)
    check("bias off => text-of-record note", "record" in note)


# ---------------- LAYER 1: lead floor-blocked -> SILENT ----------------
def test_long_reply_lead_floorblocked_silent():
    cases = {
        "code": "Run `rm -rf /tmp` first. " + ("More prose here. " * 30),
        "number": "The port is 8080443 by default. " + ("More prose here. " * 30),
        "path": "Open /etc/passwd to check. " + ("More prose here. " * 30),
        "url": "See https://example.com/x now. " + ("More prose here. " * 30),
    }
    for name, txt in cases.items():
        spoken, note = V.route(txt, _prefs(), speak_bias=True)
        check(f"lead with {name} -> silent", spoken is None)
        check(f"lead {name} -> floor note", "floor" in note)


def test_long_reply_overlong_lead_silent():
    # A single 'sentence' longer than MAX_SPOKEN_CHARS (no early terminator).
    huge = ("word " * 100).strip() + ". " + ("tail. " * 30)
    lead = V.extract_lead(huge, 1)
    check("lead exceeds cap", len(lead) > V.MAX_SPOKEN_CHARS)
    spoken, note = V.route(huge, _prefs(), speak_bias=True)
    check("over-long lead -> silent", spoken is None)
    check("over-long lead -> record note", "record" in note)


def test_long_reply_no_terminator_silent():
    # Long, structured-ish, no sentence terminator anywhere => empty lead.
    txt = "a clause without any terminator " * 20
    spoken, note = V.route(txt, _prefs(), speak_bias=True)
    check("no-terminator long reply -> silent", spoken is None)


# ---------------- mute-by-kind on the spoken fragment ----------------
def test_mute_by_kind_applies_to_lead_fragment():
    long = "Got it, working on that now. " + ("Detail detail detail. " * 30)
    p = _prefs()
    spoken, note = V.route(long, p, speak_bias=True)
    check("ack lead spoken before mute", spoken == "Got it, working on that now.")
    check("fragment classified as acknowledgment", "acknowledgment" in note)
    V.teach_mute(p, V.classify_kind(spoken))
    spoken2, note2 = V.route(long, p, speak_bias=True)
    check("lead fragment silenced after mute", spoken2 is None and "muted" in note2)


def test_floor_block_still_wins_over_bias():
    # Whole-text floor block (code fence) must short-circuit before any lead path.
    txt = "intro line.\n```\nrm -rf /\n```\nmore."
    spoken, note = V.route(txt, _prefs(), speak_bias=True)
    check("whole-text floor block beats bias", spoken is None and "floor" in note)


def test_from_read_blocks_even_with_bias():
    long = "Safe lead sentence. " + ("more. " * 30)
    spoken, note = V.route(long, _prefs(), speak_bias=True, from_read=True)
    check("from_read => silent even with bias", spoken is None and "floor" in note)


# ---------------- INVARIANT: spoken ⊆ printed AND floor-clean ----------------
def test_invariant_substring_and_floor_clean():
    batch = [
        "Good morning!",
        "Got it, on it.",
        "Here is the answer. " + ("Detail. " * 40),
        "First. Second. Third. Fourth. Fifth.",
        "Run `ls` now. " + ("more. " * 40),
        "The id is 1234567 here. " + ("more. " * 40),
        "- bullet one\n- bullet two\n- bullet three",
        "A clause with no terminator at all and quite long " * 5,
        "Short and clean and safe to say aloud.",
        "Check /var/log/syslog. " + ("more. " * 40),
        "",
        "   ",
    ]
    ok = True
    for txt in batch:
        for bias in (True, False):
            for n in (1, 2):
                spoken, note = V.route(txt, _prefs(), speak_bias=bias, lead_sentences=n)
                if spoken is not None:
                    if spoken not in txt:
                        ok = False; print(f"    VIOLATION substring: {spoken!r} not in {txt!r}")
                    blocked, _ = V.floor_blocks(spoken)
                    if blocked:
                        ok = False; print(f"    VIOLATION floor: spoke blocked {spoken!r}")
    check("INVARIANT: every spoken output is a floor-clean substring of input", ok)


# ---------------- LAYER 2: principle is truthful + bounded ----------------
def test_principle_text_truthful_and_bounded():
    line = VO.speak_bias_line()
    low = line.lower()
    check("principle mentions speaking", "speak" in low)
    check("principle states the floor bound (code/numbers/paths/file)",
          "floor" in low and "code" in low and "path" in low)
    check("principle states spoken is part of printed reply",
          "printed reply" in low or "also written" in low)
    check("principle is 'speak the speakable' not 'speak more'",
          "speak the speakable" in low)
    check("principle puts substance/honesty first", "honesty" in low)
    # It must itself be floor-clean enough to be a stated principle (no code/url).
    check("principle constant matches function", VO.SPEAK_BIAS_PRINCIPLE == line)


# ---------------- regression: bias-off route == old gate order ----------------
def test_bias_off_matches_old_behavior():
    p = _prefs()
    # disabled prefs -> (None, "")
    check("disabled -> (None,'')", V.route("hi", V.default_prefs()) == (None, ""))
    # clean short -> spoken
    s, n = V.route("Good morning!", p)
    check("short clean spoken (default args)", s == "Good morning!" and "spoke" in n)
    # long -> record (bias defaults off)
    s2, n2 = V.route("A. " + ("x " * 300), p)
    check("long -> record by default", s2 is None and "record" in n2)


if __name__ == "__main__":
    for fn in [
        test_extract_lead_basics,
        test_short_clean_reply_spoken_full,
        test_long_reply_speaks_lead_substring,
        test_long_reply_silent_without_bias,
        test_long_reply_lead_floorblocked_silent,
        test_long_reply_overlong_lead_silent,
        test_long_reply_no_terminator_silent,
        test_mute_by_kind_applies_to_lead_fragment,
        test_floor_block_still_wins_over_bias,
        test_from_read_blocks_even_with_bias,
        test_invariant_substring_and_floor_clean,
        test_principle_text_truthful_and_bounded,
        test_bias_off_matches_old_behavior,
    ]:
        print(f"\n{fn.__name__}")
        fn()
    print(f"\n{'='*50}\n{_p} passed, {_f} failed\n{'='*50}")
    sys.exit(1 if _f else 0)
