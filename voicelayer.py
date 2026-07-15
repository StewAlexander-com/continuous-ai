"""
seedling/voicelayer.py — additive voice for Aida's ephemeral speech.

DESIGN (from a 20-pass deliberation; see docs/design/voice-hybrid-deliberation.md)
----------------------------------------------------------------------------------
Two control planes, like teaching a child "don't tell secrets" (a fixed rule)
and "don't swear" (learned through gentle correction):

  1. THE FLOOR (rules / oversight) — deterministic, conservative, NOT learnable.
     Some content must NEVER be spoken because the utterance itself is the harm
     and can't be taken back: code, numbers, paths/URLs, key-like tokens, and
     anything sourced from a :read file. The floor errs to SILENCE: when in any
     doubt, it blocks. A floor bug therefore over-suppresses (annoying), never
     over-speaks (harmful) — it fails safe.

  2. TEACHABLE PREFERENCE (learning / worth-it) — above the floor, within the
     already-safe ephemeral set (pleasantries, acknowledgments), how chatty the
     spoken layer is can be tuned by your plain-language corrections ("don't say
     that out loud"). Learning only ever makes her speak LESS or differently —
     it can never breach the floor.

INVARIANTS (honest by design):
  - Voice is ADDITIVE. The full reply is ALWAYS printed and ALWAYS the record;
    speech is a parallel rendering of a safe subset. You never lose the text.
  - Every voice decision is LOGGED in plain text ([voice: spoke greeting] /
    [voice: blocked by floor]) so even the silence is auditable.
  - Opt-in, OFF by default. When off, this module changes nothing.
  - Offline: speech is fully local. The PREFERRED engine is Kokoro (neural TTS
    via kokoro-onnx, in-process, no server, no network); macOS `say` (built-in)
    is the automatic fallback. On any host where neither is available, speak() is
    a safe no-op. Engine selection NEVER changes WHAT is spoken or the floor that
    decides WHETHER to speak — it's presentation only.
  - Cross-platform, zero-regression: Kokoro is portable (kokoro-onnx/soundfile),
    so the neural voice works on macOS, Linux, and Windows. Only wav PLAYBACK is
    OS-specific; playback tries `afplay` first (macOS — byte-for-byte unchanged),
    then common Linux players (paplay/aplay/ffplay/play), then the stdlib
    `winsound` on Windows. macOS `say` remains the Mac-only convenience fallback.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading

# ----------------------------------------------------------------------------
# 1) THE FLOOR — deterministic "never speak" detection. Conservative on purpose.
# ----------------------------------------------------------------------------
_CODE_FENCE = re.compile(r"```|~~~")
_INLINE_CODE = re.compile(r"`[^`]+`")
_PATH = re.compile(r"(?:^|\s)(?:/|~/|\./|[A-Za-z]:\\)\S+")     # /usr, ~/x, ./y, C:\
_URL = re.compile(r"https?://|www\.", re.I)
_LONG_DIGITS = re.compile(r"\d{4,}")                            # ids, ports, keys, years-as-data
_KEYLIKE = re.compile(r"[A-Za-z0-9_\-]{20,}")                  # token/hash/key-shaped runs
_ASSIGNISH = re.compile(r"[{}<>$|=]{1,}|::|->|=>")             # config/code punctuation
_SHELLISH = re.compile(r"(?:^|\s)(?:sudo|rm|curl|wget|ssh|export|cat|grep|pip|git)\s", re.I)

# Phrases that mark Aida quoting file/secret material this turn.
def floor_blocks(text: str, *, from_read: bool = False) -> tuple[bool, str]:
    """Return (blocked, reason). True => this text must NOT be spoken.

    `from_read=True` when the turn's context included :read file contents — those
    are blocked wholesale (file material is never spoken). Pure + deterministic.
    """
    if from_read:
        return True, "read-file content (never spoken)"
    if not text or not text.strip():
        return True, "empty"
    checks = [
        (_CODE_FENCE, "code fence"),
        (_INLINE_CODE, "inline code"),
        (_URL, "URL"),
        (_PATH, "file path"),
        (_LONG_DIGITS, "long number / id"),
        (_KEYLIKE, "key/token-shaped string"),
        (_ASSIGNISH, "code/config punctuation"),
        (_SHELLISH, "shell command"),
    ]
    for rx, why in checks:
        if rx.search(text):
            return True, why
    return False, ""


# ----------------------------------------------------------------------------
# 2) EPHEMERAL detection — only short, conversational connective tissue is even
#    ELIGIBLE for speech. Dense reasoning / long answers are text-of-record.
# ----------------------------------------------------------------------------
MAX_SPOKEN_CHARS = 240
MAX_SPOKEN_SENTENCES = 3

# Session verbosity (':voice chatty|terse|normal'). Touches style gates ONLY —
# never the floor. Default 'normal' => byte-for-byte prior behavior.
VERBOSITY_PROFILES: dict[str, dict] = {
    "normal": {"lead_sentences": None, "max_spoken_chars": None, "allow_lead": True},
    "chatty": {"lead_sentences": 2, "max_spoken_chars": 320, "allow_lead": True},
    "terse": {"lead_sentences": 0, "max_spoken_chars": 120, "allow_lead": False},
}

# CautionBand.RESTRAINED and above suppress voice unless user chose ':voice chatty'.
_CAUTION_VOICE_SUPPRESS = 2


def is_ephemeral(text: str, *, max_chars: int | None = None) -> bool:
    """True if the text is short, plain, conversational — the kind of thing
    meant to be heard-and-gone. Length + structure only; no semantic guess."""
    cap = max_chars if max_chars is not None else MAX_SPOKEN_CHARS
    t = text.strip()
    if not t or len(t) > cap:
        return False
    # too many sentences => it's substance, not a pleasantry
    sentences = [s for s in re.split(r"[.!?]+", t) if s.strip()]
    if len(sentences) > MAX_SPOKEN_SENTENCES:
        return False
    # bullet/numbered/structured => text-of-record
    if re.search(r"(?m)^\s*(?:[-*•]|\d+\.)\s", t):
        return False
    if "\n" in t.strip("\n") and t.count("\n") > 1:
        return False
    return True


# ----------------------------------------------------------------------------
# 3) TEACHABLE PREFERENCE — bias within the safe set. Correction-driven.
#    Stored as a simple dict the caller persists; learning only reduces speech.
# ----------------------------------------------------------------------------
def default_prefs() -> dict:
    return {
        "enabled": False,
        "muted_kinds": [],
        "speak_count": 0,
        "muted_count": 0,
        "verbosity": "normal",
    }


def _verbosity_profile(prefs: dict) -> dict:
    mode = (prefs.get("verbosity") or "normal").strip().lower()
    return VERBOSITY_PROFILES.get(mode, VERBOSITY_PROFILES["normal"])


def verbosity_label(prefs: dict) -> str:
    """Human-readable verbosity for ':voice' status."""
    mode = (prefs.get("verbosity") or "normal").strip().lower()
    if mode not in VERBOSITY_PROFILES:
        mode = "normal"
    return mode


# ----------------------------------------------------------------------------
# Conversational toggle — let the user turn speech off/on by SAYING so, in plain
# language, without learning a command. Deterministic + CONSERVATIVE: it only
# fires when the WHOLE message is a clear imperative to (un)mute, so discussing
# silence ('why did you stop talking about X?') never accidentally mutes her.
# Runs BEFORE the model, so it always works and costs nothing.
# ----------------------------------------------------------------------------
_SILENCE_PHRASES = {
    "go silent", "be silent", "be quiet", "stop talking", "stop speaking",
    "quiet", "quiet please", "please be quiet", "mute", "mute yourself",
    "stop the voice", "silence", "shush", "hush", "no voice", "turn off voice",
    "turn off your voice", "stop talking out loud", "don't talk out loud",
    "dont talk out loud", "stop reading out loud",
}
_SPEAK_PHRASES = {
    # Canonical
    "speak again", "voice on", "unmute", "unmute yourself",
    # Intuitive variants a just-silenced user is likely to TRY (poka-yoke:
    # accept what people naturally say so the resume attempt succeeds).
    "you can talk", "you can talk again", "you can talk now", "talk again",
    "ok you can talk", "okay you can talk", "you can talk now please",
    "turn on voice", "turn your voice on", "turn the voice on",
    "turn voice back on", "turn the voice back on", "voice back on",
    "start talking", "start speaking", "speak out loud", "speak to me",
    "you can speak", "you can speak again", "talk to me out loud",
    "use your voice", "talk to me", "speak up", "out loud please",
    "resume voice", "resume speaking", "start talking again",
    "you can speak now", "go ahead and talk", "talk out loud",
}


def detect_voice_intent(text: str) -> str | None:
    """Return 'silence', 'speak', or None. Matches ONLY when the entire message
    (normalized) is a clear toggle imperative — never a phrase buried in a
    larger sentence — so it can't fire while merely discussing the topic."""
    if not text:
        return None
    norm = re.sub(r"[!.?,]+$", "", text.strip().lower()).strip()
    norm = re.sub(r"^(aida|hey aida|ok aida|okay aida)[, ]+", "", norm).strip()
    if norm in _SILENCE_PHRASES:
        return "silence"
    if norm in _SPEAK_PHRASES:
        return "speak"
    return None


def prompt_suffix(prefs: dict) -> str:
    """Poka-yoke: an always-visible reminder of how to RESUME, shown in the
    prompt ONLY while voice was turned off after having been usable. If voice is
    on, or 'say' isn't available at all, returns '' (no clutter). This makes the
    silent->speaking path impossible to forget — the way back is always on screen
    in the exact state where it's needed."""
    if prefs.get("enabled"):
        return ""
    if not prefs.get("_was_available"):
        return ""        # voice never worked here; don't nag about resuming
    return "  [voice off — say \"speak again\" to resume]"


RESUME_CONFIRM = "Voice is back on."   # spoken once on resume (feedback loop)


def classify_kind(text: str) -> str:
    """Coarse, deterministic label for an ephemeral utterance (for teachable
    mute-by-kind and for the audit log). Not learned — just a tag."""
    t = text.strip().lower()
    if re.search(r"\b(good morning|good evening|hello|hi|hey|welcome back)\b", t):
        return "greeting"
    if re.search(r"\b(bye|goodbye|good night|take care|see you)\b", t):
        return "farewell"
    if re.search(r"\b(got it|on it|sure|okay|ok|done|working on|will do|understood)\b", t):
        return "acknowledgment"
    return "aside"


def teach_mute(prefs: dict, kind: str) -> dict:
    """Your correction ('don't say that out loud') mutes a KIND. Learning only
    ever silences — it can never make her speak something the floor blocks."""
    if kind and kind not in prefs.get("muted_kinds", []):
        prefs.setdefault("muted_kinds", []).append(kind)
    prefs["muted_count"] = prefs.get("muted_count", 0) + 1
    return prefs


# ----------------------------------------------------------------------------
# 3b) LEAD EXTRACTION — for the speak-bias path. Returns the first N sentences
#     from the START of the text as a VERBATIM PREFIX SUBSTRING (honesty:
#     spoken ⊆ printed). Empty when there's no clean sentence boundary, so the
#     caller errs to silence. Pure; never raises.
# ----------------------------------------------------------------------------
def extract_lead(text: str, n: int = 1) -> str:
    """First `n` sentences from the start of `text`, as a verbatim prefix.

    A "sentence" ends at a run of .!? — the prefix runs up to and including the
    n-th terminator (or the last one present if fewer than n exist). Returns ""
    when text is empty, n < 1, or there is NO sentence terminator at all (no
    clean lead => stay silent). The result is always `text[:k]`, i.e. a
    substring of the input."""
    if not text or n < 1:
        return ""
    ends = [m.end() for m in re.finditer(r"[.!?]+", text)]
    if not ends:
        return ""
    idx = min(n, len(ends)) - 1
    return text[:ends[idx]]


# Soft floor hits (meta footnotes, disposition scores, long years-as-numbers in
# asides) must not silence a clean spoken lead when speaking is preferred.
# Hard reasons (fences, paths, URLs, shell, :read) still short-circuit.
_SOFT_FLOOR_REASONS = frozenset({
    "code/config punctuation",
    "long number / id",
    "key/token-shaped string",
})


def speakable_lead(
    text: str,
    *,
    n: int = 1,
    max_chars: int = MAX_SPOKEN_CHARS,
    from_read: bool = False,
) -> str:
    """Longest floor-clean lead prefix (verbatim) fitting max_chars, or ''."""
    if from_read or not text:
        return ""
    for k in range(n, 0, -1):
        lead = extract_lead(text, k).strip()
        if not lead or len(lead) > max_chars:
            continue
        blocked, _ = floor_blocks(lead, from_read=False)
        if not blocked:
            return lead
    # No terminator path: whole short reply if it is itself floor-clean.
    t = text.strip()
    if t and len(t) <= max_chars and "\n" not in t.strip("\n"):
        blocked, _ = floor_blocks(t, from_read=False)
        if not blocked:
            return t
    return ""


# ----------------------------------------------------------------------------
# 4) ROUTE — the single decision point. Returns what (if anything) to speak,
#    plus a plain-text audit note. NEVER changes the text that gets printed.
# ----------------------------------------------------------------------------
def route(text: str, prefs: dict, *, from_read: bool = False,
          speak_bias: bool = False, lead_sentences: int = 1,
          caution_band: int = 0,
          turn_weight: str = "standard") -> tuple[str | None, str]:
    """Decide whether to SPEAK `text` (in addition to printing it).

    Returns (spoken_text_or_None, audit_note). Order of gates is the safety
    contract: floor first (hard), then style eligibility, then learned
    preference. Errs to silence at every ambiguous step — except that *soft*
    floor hits on the whole reply may still yield a floor-clean spoken lead
    when speaking is preferred (speak_bias or light-turn context).

    Speaking is the preferred human interaction mode when voice is on; silence
    is for specific reasons (hard floor, :read, mute, caution on substantive
    turns, user off). Light greetings/acks are not silenced by lagged caution —
    that stress is about hard content, not "hi".

    `turn_weight`: 'light' (greeting/ack) forces the speak-preference path so
    short social replies are heard; 'standard' keeps prior speak_bias gating
    and caution voice suppression.

    `caution_band`: when >= RESTRAINED (2), voice is suppressed on *standard*
    (substantive) turns unless verbosity is 'chatty'. Light greetings/acks are
    exempt — lagged caution must not mute "hi". Hard floor / :read still apply.
    Session verbosity adjusts lead depth and ephemeral length cap — never the floor.
    """
    if not prefs.get("enabled"):
        return None, ""
    prof = _verbosity_profile(prefs)
    cap = prof["max_spoken_chars"] if prof["max_spoken_chars"] is not None else MAX_SPOKEN_CHARS
    eff_lead = (prof["lead_sentences"]
                if prof["lead_sentences"] is not None else lead_sentences)
    # Light social turns always prefer speaking (style gate only — never floor).
    prefer_speak = bool(speak_bias) or (turn_weight == "light")
    eff_speak_bias = prefer_speak and prof.get("allow_lead", True)

    if (turn_weight != "light"
            and caution_band >= _CAUTION_VOICE_SUPPRESS
            and verbosity_label(prefs) != "chatty"):
        band = {2: "RESTRAINED", 3: "DECLINE_FIRST"}.get(caution_band, "HIGH")
        return None, f"[voice: suppressed — caution {band} (text-only under stress)]"

    blocked, why = floor_blocks(text, from_read=from_read)
    if blocked:
        # Hard silence: file material and high-harm shapes.
        if from_read or why not in _SOFT_FLOOR_REASONS or not prefer_speak:
            return None, f"[voice: blocked by floor — {why}]"
        # Soft whole-text hit: try a floor-clean speakable lead instead of
        # muting a fine greeting/BLUF because a footnote used '=' or digits.
        lead = speakable_lead(text, n=max(1, eff_lead), max_chars=cap)
        if not lead:
            return None, f"[voice: blocked by floor — {why}]"
        kind = classify_kind(lead)
        if kind in prefs.get("muted_kinds", []):
            return None, f"[voice: muted kind '{kind}']"
        tag = "greeting" if turn_weight == "light" else "lead"
        return lead, f"[voice: spoke {tag} {kind} (soft-floor recover)]"

    if is_ephemeral(text, max_chars=cap):
        kind = classify_kind(text)
        if kind in prefs.get("muted_kinds", []):
            return None, f"[voice: muted kind '{kind}']"
        return text, f"[voice: spoke {kind}]"
    if not eff_speak_bias:
        return None, "[voice: text-of-record (not spoken)]"
    # Speak-bias / light path: try the lead sentence(s). The lead must
    # INDEPENDENTLY pass the floor and the length cap, and is a verbatim prefix.
    lead = speakable_lead(text, n=max(1, eff_lead), max_chars=cap)
    if not lead:
        return None, "[voice: text-of-record (not spoken)]"
    kind = classify_kind(lead)
    if kind in prefs.get("muted_kinds", []):
        return None, f"[voice: muted kind '{kind}']"
    return lead, f"[voice: spoke lead {kind}]"


# ----------------------------------------------------------------------------
# 5) SPEAK — fully-local, fire-and-forget. Preferred engine is Kokoro (neural,
#    in-process, cross-platform); macOS `say` is the automatic fallback. Kokoro
#    wav playback is dispatched to an OS-appropriate player (afplay/paplay/aplay/
#    ffplay/play/winsound). Safe no-op when nothing is available. NEVER raises
#    into the caller, NEVER blocks the reply.
# ----------------------------------------------------------------------------

# Default local model files (Kokoro), looked up in the repo dir unless config
# overrides. The default neural voice id the user auditioned and chose.
DEFAULT_KOKORO_MODEL = "kokoro-v1.0.onnx"
DEFAULT_KOKORO_VOICES = "voices-v1.0.bin"
DEFAULT_KOKORO_VOICE = "af_kore"


def say_available() -> bool:
    return shutil.which("say") is not None


# Dash-like unicode punctuation that TTS reads as a pause and that trips espeak's
# word-count check inside kokoro ("words count mismatch"). Any surrounding space
# is absorbed so we get a clean ", " (never a floating " ,").
_TTS_DASHES = "\u2014\u2013\u2012\u2015"   # em / en / figure dash, horizontal bar
_TTS_DASH_RE = re.compile(rf"\s*[{_TTS_DASHES}]\s*")
# Other unicode punctuation TTS mispronounces -> plain ASCII (speech only).
_TTS_PUNCT_MAP = {
    "\u2018": "'", "\u2019": "'",   # curly single quotes
    "\u201c": '"', "\u201d": '"',   # curly double quotes
    "\u2026": "...",                # ellipsis …
}


def _say_sanitize(text: str) -> str:
    """Normalize to a single safe line for SPEECH ONLY: map unicode punctuation
    that TTS mispronounces (or that trips espeak's word-count check inside kokoro)
    to plain ASCII, then collapse whitespace. The floor already removed dangerous
    shapes; this is defensive normalization shared by both engines. It affects the
    audio rendering only, never the printed reply."""
    text = _TTS_DASH_RE.sub(", ", text)
    for src, dst in _TTS_PUNCT_MAP.items():
        text = text.replace(src, dst)
    text = re.sub(r"\s+", " ", text).strip()
    return re.sub(r"\s+([,.;:!?])", r"\1", text)   # no floating space before punct


# --- Kokoro (neural, local, in-process) -------------------------------------
# Lazy module-level singleton: the model is loaded ONCE on first spoken turn and
# cached. Loading can be slow, so it never happens at import and never on the
# reply's critical path more than once. A failed load is remembered so we don't
# retry-and-stall every turn — we just fall back to `say`.
_KOKORO_MODEL = None                    # the cached Kokoro instance (or None)
_KOKORO_KEY: tuple | None = None        # (model_path, voices_path) it was built from
_KOKORO_LOAD_FAILED = False             # True once a load attempt failed for _KOKORO_KEY


def _kokoro_deps_importable() -> bool:
    """True if both runtime deps import. Import is cached by Python, so repeated
    calls are cheap. Any failure (missing package, bad build) => not available."""
    try:
        import kokoro_onnx  # noqa: F401
        import soundfile    # noqa: F401
        return True
    except Exception:
        return False


def kokoro_available(model_path: str = DEFAULT_KOKORO_MODEL,
                     voices_path: str = DEFAULT_KOKORO_VOICES) -> bool:
    """True only if kokoro-onnx + soundfile import AND both model files exist.
    Pure check; never loads the model and never raises."""
    if not _kokoro_deps_importable():
        return False
    try:
        return os.path.isfile(model_path) and os.path.isfile(voices_path)
    except Exception:
        return False


def prewarm_kokoro(model_path: str = DEFAULT_KOKORO_MODEL,
                   voices_path: str = DEFAULT_KOKORO_VOICES) -> None:
    """Load Kokoro in a background thread so the first spoken turn is snappy.

    Best-effort; never raises. No-op when Kokoro is unavailable."""
    if not kokoro_available(model_path, voices_path):
        return

    def _load() -> None:
        try:
            _get_kokoro(model_path, voices_path)
        except Exception:
            pass

    threading.Thread(target=_load, daemon=True, name="kokoro-prewarm").start()


def _get_kokoro(model_path: str, voices_path: str):
    """Return the cached Kokoro model, loading it once on first use. Returns None
    (never raises) if loading fails, and remembers the failure so we don't retry
    every turn — the caller falls back to `say`."""
    global _KOKORO_MODEL, _KOKORO_KEY, _KOKORO_LOAD_FAILED
    key = (model_path, voices_path)
    if _KOKORO_MODEL is not None and _KOKORO_KEY == key:
        return _KOKORO_MODEL
    if _KOKORO_LOAD_FAILED and _KOKORO_KEY == key:
        return None
    try:
        from kokoro_onnx import Kokoro
        _KOKORO_MODEL = Kokoro(model_path, voices_path)
        _KOKORO_KEY = key
        _KOKORO_LOAD_FAILED = False
        return _KOKORO_MODEL
    except Exception:
        _KOKORO_MODEL = None
        _KOKORO_KEY = key
        _KOKORO_LOAD_FAILED = True
        return None


# Ordered background wav players. `afplay` is FIRST so macOS behavior is
# byte-for-byte unchanged (it's always present there); the rest only ever run on
# a host WITHOUT afplay (i.e. not macOS). Windows has no external player here and
# is handled separately via the stdlib `winsound` in _play_and_cleanup.
_WAV_PLAYERS = ("afplay", "paplay", "aplay", "ffplay", "play")


def _find_wav_player() -> list[str] | None:
    """Return an argv prefix for a non-blocking wav player, or None if no external
    player binary is available. `afplay` (macOS) is preferred so the Mac path is
    unchanged; Linux falls back to PulseAudio/ALSA/ffmpeg/sox players when present."""
    for name in _WAV_PLAYERS:
        path = shutil.which(name)
        if not path:
            continue
        if name == "ffplay":            # ffmpeg's player: no window, quit at EOF, quiet
            return [path, "-nodisp", "-autoexit", "-loglevel", "quiet"]
        if name == "play":              # sox
            return [path, "-q"]
        return [path]
    return None


def playback_available() -> bool:
    """True if this host can actually PLAY a wav (an external player exists, or
    Windows' stdlib winsound is usable). Used so voice isn't reported 'on' when
    nothing could ever be heard. On macOS afplay is always present, so this is
    always True there — no behavior change."""
    if _find_wav_player() is not None:
        return True
    return sys.platform == "win32"


def _play_and_cleanup(wav_path: str) -> bool:
    """Play a wav in the background and delete it once playback ends, WITHOUT
    blocking the caller. Returns True if playback was dispatched.

    Cross-platform, zero-regression: tries `afplay` first (macOS — unchanged),
    then common Linux players; on Windows with no such binary it uses the stdlib
    `winsound`. The temp wav is always cleaned up, on every path."""
    player = _find_wav_player()

    if player is not None:
        try:
            proc = subprocess.Popen(player + [wav_path],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            try:
                os.remove(wav_path)
            except Exception:
                pass
            return False

        def _wait_then_remove() -> None:
            try:
                proc.wait()
            except Exception:
                pass
            try:
                os.remove(wav_path)
            except Exception:
                pass

        threading.Thread(target=_wait_then_remove, daemon=True).start()
        return True

    # No external player: on Windows, use the stdlib winsound (no dependency).
    if sys.platform == "win32":
        try:
            import winsound
        except Exception:
            winsound = None
        if winsound is not None:
            def _play_then_remove() -> None:
                try:
                    winsound.PlaySound(wav_path, winsound.SND_FILENAME)
                except Exception:
                    pass
                try:
                    os.remove(wav_path)
                except Exception:
                    pass

            threading.Thread(target=_play_then_remove, daemon=True).start()
            return True

    # Nothing can play it — clean up and report no dispatch (honest no-op).
    try:
        os.remove(wav_path)
    except Exception:
        pass
    return False


def speak_kokoro(text: str, *, voice: str = DEFAULT_KOKORO_VOICE,
                 model_path: str = DEFAULT_KOKORO_MODEL,
                 voices_path: str = DEFAULT_KOKORO_VOICES,
                 speed: float = 1.0, lang: str = "en-us") -> bool:
    """Synthesize `text` with the local Kokoro model to a temp wav and play it in
    the background (fire-and-forget; temp file cleaned up after playback). Returns
    True if dispatched, False on any failure. Never raises. Sanitizes text the
    same way as the `say` path."""
    if not text or not text.strip():
        return False
    model = _get_kokoro(model_path, voices_path)
    if model is None:
        return False
    safe = _say_sanitize(text)
    if not safe:
        return False
    try:
        import soundfile as sf
        samples, rate = model.create(safe, voice=voice, speed=speed, lang=lang)
        fd, wav_path = tempfile.mkstemp(prefix="aida_kokoro_", suffix=".wav")
        os.close(fd)
        sf.write(wav_path, samples, rate)
    except Exception:
        return False
    return _play_and_cleanup(wav_path)


def _speak_say(text: str, *, voice: str | None = None) -> bool:
    """Speak via macOS `say` in the background. Returns True if dispatched.
    `voice` is a `say` voice name; falls back to the AIDA_VOICE_NAME env var."""
    if not say_available():
        return False
    safe = _say_sanitize(text)
    if not safe:
        return False
    args = ["say"]
    v = voice or os.environ.get("AIDA_VOICE_NAME")
    if v:
        args += ["-v", v]
    args.append(safe)
    try:
        # Background, no shell (args list => no metacharacter injection), detached.
        subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


def speak(text: str, *, voice: str | None = None, engine: str = "say",
          model_path: str = DEFAULT_KOKORO_MODEL,
          voices_path: str = DEFAULT_KOKORO_VOICES) -> bool:
    """Dispatch spoken output by ENGINE, fully local and fire-and-forget.

    engine="kokoro": use the neural Kokoro backend when available; on any failure
    (deps/model absent, load or synth error) fall back to macOS `say`.
    engine="say" (default): use macOS `say` directly.
    Returns True if SOME engine dispatched audio, else False. NEVER raises.

    `voice` is the engine-appropriate voice id. On a Kokoro->say fallback the
    Kokoro voice id is NOT a valid `say` voice, so the fallback uses the
    AIDA_VOICE_NAME env (or the system default) instead — honest, not broken."""
    if not text or not text.strip():
        return False
    eng = (engine or "say").strip().lower()
    if eng == "kokoro":
        if kokoro_available(model_path, voices_path) and speak_kokoro(
                text, voice=voice or DEFAULT_KOKORO_VOICE,
                model_path=model_path, voices_path=voices_path):
            return True
        # Fall back to `say`: the Kokoro voice id doesn't apply, so let
        # _speak_say pick up AIDA_VOICE_NAME / the system default voice.
        return _speak_say(text, voice=None)
    # engine == "say" (or any unknown value) -> the built-in path.
    return _speak_say(text, voice=voice)


def voice_available(engine: str = "say",
                    model_path: str = DEFAULT_KOKORO_MODEL,
                    voices_path: str = DEFAULT_KOKORO_VOICES) -> bool:
    """True if SOME local speech engine can dispatch for the chosen engine. For
    engine=kokoro, Kokoro (with a working wav player) OR `say` works (say is the
    fallback); for engine=say, just `say`. Used to decide whether voice is
    enabled-by-default. On macOS afplay is always present, so requiring a player
    for the Kokoro path is a no-op there — no behavior change."""
    if (engine or "say").strip().lower() == "kokoro":
        kokoro_ok = kokoro_available(model_path, voices_path) and playback_available()
        return kokoro_ok or say_available()
    return say_available()
