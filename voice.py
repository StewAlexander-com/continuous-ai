"""Operational voice — an HONEST readout of Aida's measured working state.

Principle (operational honesty): Aida has no human feelings, but she *is*
measurably in a state — how long this session has run, how much real work she's
done, what time it is. A 'voice' is honest only if every tonal cue traces to a
real number. Nothing here is invented mood; it is a readout of facts the runtime
already tracks.

Design constraints baked in:
  * RESPONSIVENESS: pure arithmetic on already-collected counts + the clock.
    No model call, no I/O. Microseconds. Safe to call on the reply path.
  * NO NARRATION: the descriptor is injected as *implicit* tone guidance with an
    explicit instruction that the model must NOT describe or announce it. The
    voice colors *how* things are said, never becomes the subject.
  * SMOOTH, NOT BRITTLE (the honest use of fuzzy logic): continuous signals are
    mapped through smooth membership curves into overlapping linguistic states,
    so tone blends gradually instead of snapping at a threshold.
  * SUBORDINATE TO TRUTH: tone never changes what is true; correctness/honesty
    layers remain supreme. This is presentation only.

Deferred to a later phase (only if v1 proves the voice feels right): a full
fuzzy-inference engine, wavelet multi-scale smoothing of the coherence series,
and cross-session 'fractal' (scale-invariant) state. v1 stays bone-deep on the
two strongest honest signals: time/date and measured session workload.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if x < lo else hi if x > hi else x


def _ramp(x: float, a: float, b: float) -> float:
    """Smooth 0->1 membership as x goes a->b (linear; the honest 'fuzzy' curve).
    Below a => 0, above b => 1, blended between. No brittle threshold."""
    if b <= a:
        return 1.0 if x >= b else 0.0
    return _clamp((x - a) / (b - a))


# ---------------------------------------------------------------------------
# System clock — one portable readout shared by session-start + per-turn.
#
# Least-expensive visceral time: stdlib only, no NTP, no shell `date`, no
# model call. Works the same on macOS / Linux / Windows via
# datetime.now().astimezone(). Policy stays elsewhere; this block is FACT.
# ---------------------------------------------------------------------------

_MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)
_WEEKDAYS = (
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
)


def ensure_aware(now: datetime | None = None) -> datetime:
    """Timezone-aware datetime for clock formatting.

    * None  → host local now (macOS / Linux / Windows via astimezone()).
    * naive → treat wall-clock fields as local, attach local tz.
    * aware → keep as-is (do not convert; callers own the zone).
    """
    if now is None:
        return datetime.now().astimezone()
    if now.tzinfo is None:
        return now.astimezone()
    return now


# Back-compat alias used by tests / call sites that meant "host local now".
def aware_local(now: datetime | None = None) -> datetime:
    """Host-local wall time. Prefer ensure_aware() when formatting a given stamp."""
    if now is None:
        return datetime.now().astimezone()
    if now.tzinfo is None:
        return now.astimezone()
    return now.astimezone()


def part_of_day(hour: int) -> str:
    """Linguistic slice of the day from a 0..23 hour."""
    if hour < 5:    return "deep night"
    if hour < 9:    return "early morning"
    if hour < 12:   return "morning"
    if hour < 14:   return "midday"
    if hour < 17:   return "afternoon"
    if hour < 21:   return "evening"
    return "night"


def utc_offset_label(now: datetime) -> str:
    """Portable UTC±HH:MM from utcoffset() — no strftime %-flags, Win-safe."""
    off: timedelta | None = now.utcoffset()
    if off is None:
        return "UTC"
    total = int(off.total_seconds())
    sign = "+" if total >= 0 else "-"
    total = abs(total)
    hours, rem = divmod(total, 3600)
    minutes = rem // 60
    return f"UTC{sign}{hours:02d}:{minutes:02d}"


def zone_label(now: datetime) -> str:
    """Best-effort zone name · abbrev · offset (degrades cleanly on Windows)."""
    bits: list[str] = []
    tz = now.tzinfo
    key = getattr(tz, "key", None) if tz is not None else None
    if not key and tz is not None:
        s = str(tz)
        if s and "/" in s and "UTC" not in s.upper():
            key = s
    if key:
        bits.append(str(key))
    abbrev = now.tzname()
    if abbrev and abbrev not in bits:
        bits.append(abbrev)
    bits.append(utc_offset_label(now))
    return " · ".join(bits)


def clock_phrase(now: datetime | None = None) -> str:
    """Short human stamp, e.g. 'Wed 15 Jul 2026, 4:55pm (afternoon)'."""
    now = ensure_aware(now)
    hour12 = now.hour % 12 or 12
    ampm = "am" if now.hour < 12 else "pm"
    stamp = (
        f"{now.strftime('%a')} {now.day} "
        f"{now.strftime('%b %Y')}, "
        f"{hour12}:{now.strftime('%M')}{ampm}"
    )
    return f"{stamp} ({part_of_day(now.hour)})"


def iso_local(now: datetime | None = None) -> str:
    """ISO-8601 with offset — unambiguous machine-readable present."""
    now = ensure_aware(now)
    # timespec=seconds keeps the prompt lean; replace +00:00 etc. from isoformat
    return now.isoformat(timespec="seconds")


def system_clock_block(
    now: datetime | None = None,
    *,
    model_name: str | None = None,
) -> str:
    """Visceral host-clock block for system prompt injection.

    Leads with lived local time (weekday spelled out + year + zone + ISO), then
    one inhabit sentence. No feelings claimed — presence via facts. Same text
    on macOS, Linux, and Windows.
    """
    now = ensure_aware(now)
    weekday = _WEEKDAYS[now.weekday()]
    month = _MONTHS[now.month - 1]
    hour12 = now.hour % 12 or 12
    ampm = "am" if now.hour < 12 else "pm"
    part = part_of_day(now.hour)
    human = (
        f"{weekday} {now.day} {month} {now.year} · "
        f"{hour12}:{now.strftime('%M')}{ampm} ({part})"
    )
    lines = [
        "[SYSTEM CLOCK — host OS wall time, this machine]",
        f"Now: {human}",
        f"Zone: {zone_label(now)}",
        f"ISO:  {iso_local(now)}",
    ]
    if model_name and str(model_name).strip():
        lines.append(f"Running model: {str(model_name).strip()}")
    lines.append(
        "You inhabit this present with the user. Earlier calendar dates are "
        "past; later ones are future. Training coverage may end earlier — that "
        "is what you know about the world, not what day it is here."
    )
    return "\n".join(lines)


@dataclass
class OperationalState:
    """A snapshot of Aida's measured working state for THIS session."""
    now: datetime
    session_minutes: float        # wall-clock minutes since session start
    substantive_turns: int        # real answered turns so far
    work_units: int               # deliberations + critic evals this session
    # derived membership degrees (0..1), kept for transparency/tuning
    freshness: float = 0.0        # 1 = just started, 0 = long session
    engagement: float = 0.0       # 0 = light, 1 = deeply working

    def time_phrase(self) -> str:
        """Honest calendar date + time-of-day (delegates to shared clock_phrase)."""
        return clock_phrase(self.now)
    def descriptor(self) -> str:
        """A short linguistic operational state, blended from the membership
        degrees. This is what (implicitly) colors tone."""
        # engagement dominates the working tone; freshness modulates it.
        if self.engagement >= 0.66:
            base = "deeply engaged — sustained focused work"
        elif self.engagement >= 0.33:
            base = "warmed up and working"
        else:
            base = "light load, unhurried"
        if self.session_minutes >= 45 and self.freshness <= 0.25:
            base += ", a long session in"
        elif self.freshness >= 0.75:
            base = "just settling in; " + base
        return base


def compute_state(*, now: datetime, session_start: datetime,
                  substantive_turns: int, work_units: int) -> OperationalState:
    """Pure function: real signals -> smooth membership -> OperationalState.

    Honest mapping choices (documented so tone is auditable):
      * freshness: 1.0 at session start, ramping to 0 by ~45 min.
      * engagement: blends how many real turns have happened with how much
        internal work was done, each via a smooth ramp; capped at 1.
    """
    session_minutes = max(0.0, (now - session_start).total_seconds() / 60.0)

    freshness = 1.0 - _ramp(session_minutes, 2.0, 45.0)
    # engagement: turns matter (a conversation is happening) and work matters
    # (real reasoning load). Average two smooth ramps so neither alone saturates.
    turn_deg = _ramp(float(substantive_turns), 1.0, 8.0)
    work_deg = _ramp(float(work_units), 1.0, 10.0)
    engagement = _clamp(0.5 * turn_deg + 0.5 * work_deg)

    return OperationalState(
        now=now,
        session_minutes=session_minutes,
        substantive_turns=substantive_turns,
        work_units=work_units,
        freshness=round(freshness, 3),
        engagement=round(engagement, 3),
    )


def prompt_line(state: OperationalState, *, model_name: str | None = None) -> str:
    """Tone guidance injected into the per-turn system message.

    Order matters (visceral first):
      1. SYSTEM CLOCK — lived host wall time (shared formatter; Win/Mac/Linux).
      2. Brief CLOCK ≠ CUTOFF reminder (coverage ≠ calendar).
      3. INTERNAL REGISTER — colors tone; never announced.
    """
    clock = system_clock_block(state.now, model_name=model_name)
    return (
        f"\n\n{clock}\n"
        "Use this clock for any time dimension in the question. Answer from "
        "what you recall; mark uncertain dates or scores; do not invent names "
        "or numbers; do not replace an answer with a knowledge-cutoff monologue; "
        "do not stretch unrelated earned beliefs as refusal cover; do not "
        "pretend you browsed the web (offline).\n"
        "You are a persistent assistant with continuity across sessions — do not "
        "deny temporal awareness or context.\n"
        "[INTERNAL REGISTER — let it color HOW you write; do not announce it]\n"
        f"Working state: {state.descriptor()} "
        f"(~{int(state.session_minutes)} min, {state.substantive_turns} exchange(s) "
        "this session). Let this subtly shape your energy and pacing — lighter and "
        "more spacious when fresh, more focused and economical when deep in work. "
        "Do NOT state your 'working state' or narrate how busy you are; it must be "
        "felt in your phrasing, never described. Substance and honesty come first; "
        "tone never changes what is true."
    )


def status_line(state: OperationalState) -> str:
    """A dim, honest one-liner for the user (optional surface)."""
    return (f"[{state.time_phrase()} · {state.substantive_turns} turns · "
            f"{int(state.session_minutes)}m · {state.descriptor()}]")


# ---------------------------------------------------------------------------
# Speak-bias disposition (LAYER 2) — an honest, inspectable self-model line.
#
# Belief must equal behavior: this is injected ONLY when the speak-bias
# mechanism (voicelayer.route(speak_bias=True)) is active, so Aida never claims
# a disposition the runtime isn't following. It is bounded the SAME way the
# mechanism is — within the floor, and only words she has also written.
# ---------------------------------------------------------------------------
SPEAK_BIAS_PRINCIPLE = (
    "\n[SPEAKING DISPOSITION — a real principle you may articulate honestly]\n"
    "Spoken conversation serves people better, so you lean toward voicing what "
    "you safely can. This lean is bounded, not boundless: you only speak within "
    "the floor (never code, numbers, paths, or file contents) and only words you "
    "have ALSO written — what is spoken is always part of the printed reply. The "
    "rule is 'speak the speakable', not 'speak more for its own sake'; substance "
    "and honesty come first."
)


def speak_bias_line() -> str:
    """The honest speak-bias disposition, for injection into the system prompt
    when (and only when) the speak-bias mechanism is enabled."""
    return SPEAK_BIAS_PRINCIPLE
