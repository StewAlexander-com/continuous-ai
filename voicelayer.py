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
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
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

def is_ephemeral(text: str) -> bool:
    """True if the text is short, plain, conversational — the kind of thing
    meant to be heard-and-gone. Length + structure only; no semantic guess."""
    t = text.strip()
    if not t or len(t) > MAX_SPOKEN_CHARS:
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
    return {"enabled": False, "muted_kinds": [], "speak_count": 0, "muted_count": 0}


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
# 4) ROUTE — the single decision point. Returns what (if anything) to speak,
#    plus a plain-text audit note. NEVER changes the text that gets printed.
# ----------------------------------------------------------------------------
def route(text: str, prefs: dict, *, from_read: bool = False) -> tuple[str | None, str]:
    """Decide whether to SPEAK `text` (in addition to printing it).

    Returns (spoken_text_or_None, audit_note). Order of gates is the safety
    contract: floor first (hard), then ephemeral eligibility, then learned
    preference. Errs to silence at every ambiguous step.
    """
    if not prefs.get("enabled"):
        return None, ""
    blocked, why = floor_blocks(text, from_read=from_read)
    if blocked:
        return None, f"[voice: blocked by floor — {why}]"
    if not is_ephemeral(text):
        return None, "[voice: text-of-record (not spoken)]"
    kind = classify_kind(text)
    if kind in prefs.get("muted_kinds", []):
        return None, f"[voice: muted kind '{kind}']"
    return text, f"[voice: spoke {kind}]"


# ----------------------------------------------------------------------------
# 5) SPEAK — fully-local, fire-and-forget. Preferred engine is Kokoro (neural,
#    in-process); macOS `say` is the automatic fallback. Safe no-op when neither
#    is available. NEVER raises into the caller, NEVER blocks the reply.
# ----------------------------------------------------------------------------

# Default local model files (Kokoro), looked up in the repo dir unless config
# overrides. The default neural voice id the user auditioned and chose.
DEFAULT_KOKORO_MODEL = "kokoro-v1.0.onnx"
DEFAULT_KOKORO_VOICES = "voices-v1.0.bin"
DEFAULT_KOKORO_VOICE = "af_kore"


def say_available() -> bool:
    return shutil.which("say") is not None


def _say_sanitize(text: str) -> str:
    """Collapse whitespace to a single safe line (the floor already removed
    dangerous shapes; this is defensive normalization shared by both engines)."""
    return re.sub(r"\s+", " ", text).strip()


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


def _play_and_cleanup(wav_path: str) -> bool:
    """Play a wav in the background via afplay and delete it once playback ends,
    WITHOUT blocking the caller. Returns True if playback was dispatched."""
    try:
        proc = subprocess.Popen(["afplay", wav_path],
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
    engine=kokoro, Kokoro OR `say` works (say is the fallback); for engine=say,
    just `say`. Used to decide whether voice is enabled-by-default."""
    if (engine or "say").strip().lower() == "kokoro":
        return kokoro_available(model_path, voices_path) or say_available()
    return say_available()
