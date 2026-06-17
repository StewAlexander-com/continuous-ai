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
from datetime import datetime


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if x < lo else hi if x > hi else x


def _ramp(x: float, a: float, b: float) -> float:
    """Smooth 0->1 membership as x goes a->b (linear; the honest 'fuzzy' curve).
    Below a => 0, above b => 1, blended between. No brittle threshold."""
    if b <= a:
        return 1.0 if x >= b else 0.0
    return _clamp((x - a) / (b - a))


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
        """Honest human time-of-day, e.g. 'Wed 6:15pm (early evening)'."""
        h = self.now.hour
        if h < 5:    part = "deep night"
        elif h < 9:  part = "early morning"
        elif h < 12: part = "morning"
        elif h < 14: part = "midday"
        elif h < 17: part = "afternoon"
        elif h < 21: part = "evening"
        else:        part = "night"
        stamp = self.now.strftime("%a %-I:%M%p").replace("AM", "am").replace("PM", "pm")
        return f"{stamp} ({part})"

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


def prompt_line(state: OperationalState) -> str:
    """Tone guidance injected into the per-turn system message.

    Two cleanly SEPARATED parts (conflating them was a bug — it made the model
    suppress time awareness along with mood narration):
      1. AWARENESS the model may use freely and naturally (the real date/time).
      2. An INTERNAL working register that colors tone but is not announced.
    """
    return (
        "\n\n[CURRENT CONTEXT — real, you may reference this naturally]\n"
        f"It is {state.time_phrase()}. You DO have access to the current date and "
        "time and may acknowledge it naturally when relevant (e.g. 'this evening'). "
        "You are a persistent assistant with continuity across sessions — you are "
        "NOT a stateless chatbot; do not deny having temporal awareness or context.\n"
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
