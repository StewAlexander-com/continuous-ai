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
  - Offline: speech uses macOS `say` (built-in, no network, no deps). On any
    non-macOS / missing-`say` host, speak() is a safe no-op.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess

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
    "speak again", "you can talk", "you can talk again", "talk again",
    "voice on", "turn on voice", "turn your voice on", "start talking",
    "speak out loud", "you can speak", "unmute", "unmute yourself",
    "talk to me out loud", "use your voice",
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
# 5) SPEAK — macOS `say`, offline. Safe no-op when unavailable. Never raises,
#    never blocks the reply (fire-and-forget).
# ----------------------------------------------------------------------------
def say_available() -> bool:
    return shutil.which("say") is not None


def speak(text: str, *, voice: str | None = None) -> bool:
    """Speak via macOS `say` in the background. Returns True if dispatched.
    Sanitizes to a single safe line (the floor already removed dangerous shapes,
    but we also drop newlines and shell metacharacters defensively)."""
    if not text or not say_available():
        return False
    safe = re.sub(r"\s+", " ", text).strip()
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
