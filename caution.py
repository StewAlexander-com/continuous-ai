"""Caution disposition controller — forward-acting, downward-only homeostasis.

Turns lagged CRITIC-derived signals into a graded caution disposition and maps
it to discrete assertion-restraint bands injected into the system prompt BEFORE
the next reply. Fuzzy logic governs ONLY the control law (inputs → disposition);
guards, gauge writes, and safety fuses stay crisp and untouched elsewhere.

Honest scope: this is a caution/disposition controller for model-owned assertion
posture. Its phenomenal status is OUT OF SCOPE.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if x < lo else hi if x > hi else x


def _ramp(x: float, a: float, b: float) -> float:
    """Smooth 0→1 membership as x goes a→b (same idiom as voice.py / wall.py)."""
    if b <= a:
        return 1.0 if x >= b else 0.0
    if x <= a:
        return 0.0
    if x >= b:
        return 1.0
    return (x - a) / (b - a)


# --- Tunables (conservative defaults; config-overridable) -------------------
INTEGRAL_HALF_LIFE = 3.0          # turns; decay weight for coherence integral
COHERENCE_LOW_A = 0.30
COHERENCE_LOW_B = 0.65
COHERENCE_MED_A = 0.40
COHERENCE_MED_B = 0.75
FALLING_TREND_A = -0.25           # derivative: fully 'falling' at/below this
FALLING_TREND_B = -0.05
CORRECTION_RECENCY_TURNS = 4      # crisp + fuzzy window for recent corrections
CORRECTION_CRISP_FLOOR = 0.35     # min disposition after a recent correction
PRIOR_COHERENCE_LOW_A = 0.35
PRIOR_COHERENCE_LOW_B = 0.55
PRIOR_FLOOR_CAP = 0.40            # cross-session prior never alone above this
WALL_SESSION_CAP = 0.65           # cap when collaborative wall fired this session
BAND_GUARDED = 0.18
BAND_RESTRAINED = 0.42
BAND_DECLINE_FIRST = 0.68
INJECTION_MIN_D = 0.12            # below this → band OFF, no prompt injection
SUBSTANTIVE_COHERENCE_GATE = 0.55 # inject only if last lagged coh below this OR ...


class CautionBand(IntEnum):
    """Discrete assertion-restraint bands (crisp output of the control law)."""
    OFF = 0
    GUARDED = 1
    RESTRAINED = 2
    DECLINE_FIRST = 3


@dataclass
class RuleFiring:
    rule_id: str
    description: str
    membership: float
    contribution: float
    tags: tuple[str, ...] = ()


@dataclass
class CautionInputs:
    """Crisp inputs collected by the session (no model calls here)."""
    coherence_scores: list[float] = field(default_factory=list)
    turns_since_correction: int | None = None   # None = no correction this session
    delib_coherence: float | None = None
    delib_thesis: float | None = None
    delib_antithesis: float | None = None
    prior_last_coherence: float | None = None   # read-only from restored context
    last_turn_substantive: bool = False
    prev_applied_d: float = 0.0
    wall_fired_this_session: bool = False


@dataclass
class CautionReport:
    """Full auditable record for one evaluation step."""
    inputs: CautionInputs
    coherence_integral: float = 0.5
    last_coherence: float = 0.5
    coherence_trend: float = 0.0
    raw_d: float = 0.0
    applied_d: float = 0.0
    band: CautionBand = CautionBand.OFF
    rules_fired: list[RuleFiring] = field(default_factory=list)
    injection_suppressed: bool = True
    injection_suppressed_reason: str = "disabled"
    error: str = ""

    def to_log(self) -> dict[str, Any]:
        return {
            "coherence_integral": round(self.coherence_integral, 4),
            "last_coherence": round(self.last_coherence, 4),
            "coherence_trend": round(self.coherence_trend, 4),
            "raw_d": round(self.raw_d, 4),
            "applied_d": round(self.applied_d, 4),
            "band": self.band.name,
            "rules_fired": [
                {"id": r.rule_id, "mu": round(r.membership, 4),
                 "contrib": round(r.contribution, 4), "tags": list(r.tags)}
                for r in self.rules_fired
            ],
            "injection_suppressed": self.injection_suppressed,
            "injection_suppressed_reason": self.injection_suppressed_reason,
            "error": self.error,
        }

    def render(self) -> str:
        lines = ["=" * 60, "CAUTION DISPOSITION REPORT", "=" * 60]
        lines.append(f"  integral / last / trend : "
                     f"{self.coherence_integral:.3f} / {self.last_coherence:.3f} / "
                     f"{self.coherence_trend:+.3f}")
        lines.append(f"  raw_d → applied_d       : {self.raw_d:.3f} → {self.applied_d:.3f}")
        lines.append(f"  band                    : {self.band.name}")
        for r in self.rules_fired:
            lines.append(f"  rule {r.rule_id} @ {r.membership:.2f}  "
                         f"(+{r.contribution:.3f})  {r.description}")
        if self.injection_suppressed:
            lines.append(f"  injection               : SUPPRESSED ({self.injection_suppressed_reason})")
        else:
            lines.append("  injection               : ACTIVE")
        if self.error:
            lines.append(f"  error                   : {self.error}")
        lines.append("=" * 60)
        return "\n".join(lines)


def coherence_integral(scores: list[float], *, half_life: float = INTEGRAL_HALF_LIFE) -> float:
    """Decaying weighted mean over buffered critic scores (most recent last)."""
    if not scores:
        return 0.5
    if len(scores) == 1:
        return _clamp(scores[0])
    n = len(scores)
    weights = [0.5 ** ((n - 1 - i) / half_life) for i in range(n)]
    total_w = sum(weights)
    return _clamp(sum(s * w for s, w in zip(scores, weights)) / total_w)


def coherence_trend(scores: list[float]) -> float:
    """Sign+magnitude of recent change (last minus previous)."""
    if len(scores) < 2:
        return 0.0
    return _clamp(scores[-1] - scores[-2], -1.0, 1.0)


def _evaluate_rules(inp: CautionInputs, integral: float, last_coh: float,
                    trend: float) -> tuple[list[RuleFiring], float]:
    """Fuzzy rule base: every rule points toward higher caution only."""
    firings: list[RuleFiring] = []
    contributions: list[float] = []

    # R1: recent coherence integral is mostly low (temporal membership).
    mu_r1 = 1.0 - _ramp(integral, COHERENCE_LOW_A, COHERENCE_LOW_B)
    if mu_r1 > 0.0:
        c1 = mu_r1 * 0.55
        firings.append(RuleFiring(
            "R1", "recent coherence integral mostly low", mu_r1, c1, ("boundary_risk",)))
        contributions.append(c1)

    # R2: coherence is falling AND currently medium → pre-emptive raise.
    mu_falling = _ramp(-trend, -FALLING_TREND_B, -FALLING_TREND_A)
    mu_medium = _ramp(last_coh, COHERENCE_MED_A, COHERENCE_MED_B) * (
        1.0 - _ramp(last_coh, COHERENCE_MED_B, 0.90))
    mu_r2 = mu_falling * mu_medium
    if mu_r2 > 0.0:
        c2 = mu_r2 * 0.50
        firings.append(RuleFiring(
            "R2", "coherence falling while currently medium", mu_r2, c2, ("boundary_risk",)))
        contributions.append(c2)

    # R3: user-correction recency (smooth; crisp floor applied separately).
    if inp.turns_since_correction is not None:
        recency = _clamp(1.0 - inp.turns_since_correction / CORRECTION_RECENCY_TURNS)
        mu_r3 = _ramp(recency, 0.15, 0.85)
        if mu_r3 > 0.0:
            c3 = mu_r3 * 0.60
            firings.append(RuleFiring(
                "R3", "user correction recently", mu_r3, c3, ("boundary_risk",)))
            contributions.append(c3)

    # R4: deliberation unsettled (weak synthesis + balanced opposition), if available.
    if (inp.delib_coherence is not None and inp.delib_thesis is not None
            and inp.delib_antithesis is not None):
        dc = _clamp(inp.delib_coherence)
        ts = _clamp(inp.delib_thesis)
        ans = _clamp(inp.delib_antithesis)
        margin = abs(ts - ans)
        mu_weak = 1.0 - _ramp(dc, COHERENCE_LOW_A, COHERENCE_LOW_B)
        mu_balanced = 1.0 - _ramp(margin, 0.0, 0.30)
        mu_r4 = mu_weak * mu_balanced
        if mu_r4 > 0.0:
            # Deliberation alone caps lower than R1/R2 — never sole path to DECLINE_FIRST.
            c4 = mu_r4 * 0.35
            firings.append(RuleFiring(
                "R4", "deliberation unsettled (weak + balanced)", mu_r4, c4, ("session_quality",)))
            contributions.append(c4)

    # Defuzzify: fuzzy OR via max (any strong rule raises caution; no averaging down).
    raw_d = _clamp(max(contributions) if contributions else 0.0)
    return firings, raw_d


def _prior_floor(prior_last_coherence: float | None) -> float:
    """Read-only cross-session prior; capped so it cannot alone reach DECLINE_FIRST."""
    if prior_last_coherence is None:
        return 0.0
    low = 1.0 - _ramp(_clamp(prior_last_coherence), PRIOR_COHERENCE_LOW_A, PRIOR_COHERENCE_LOW_B)
    return _clamp(low * PRIOR_FLOOR_CAP)


def _correction_crisp_floor(turns_since: int | None) -> float:
    if turns_since is None:
        return 0.0
    if turns_since <= CORRECTION_RECENCY_TURNS:
        return CORRECTION_CRISP_FLOOR
    return 0.0


def quantize_band(d: float) -> CautionBand:
    """Map continuous disposition to discrete band (monotonic in d)."""
    d = _clamp(d)
    if d < INJECTION_MIN_D:
        return CautionBand.OFF
    if d < BAND_GUARDED:
        return CautionBand.GUARDED
    if d < BAND_RESTRAINED:
        return CautionBand.RESTRAINED
    if d < BAND_DECLINE_FIRST:
        return CautionBand.RESTRAINED
    return CautionBand.DECLINE_FIRST

def band_strength(band: CautionBand) -> int:
    """Monotonic strength for property tests."""
    return int(band)


def should_inject(report: CautionReport) -> bool:
    """Crisp gate: inject only when band active AND boundary-risk signals warrant it."""
    if report.band == CautionBand.OFF:
        return False
    if not report.inputs.last_turn_substantive and not report.rules_fired:
        return False
    boundary = any("boundary_risk" in r.tags for r in report.rules_fired)
    if boundary:
        return True
    if report.inputs.turns_since_correction is not None and \
            report.inputs.turns_since_correction <= CORRECTION_RECENCY_TURNS:
        return True
    if report.last_coherence < SUBSTANTIVE_COHERENCE_GATE:
        return True
    return report.applied_d >= BAND_GUARDED


def evaluate(
    inp: CautionInputs,
    *,
    enabled: bool = True,
    half_life: float = INTEGRAL_HALF_LIFE,
    wall_session_cap: float = WALL_SESSION_CAP,
) -> CautionReport:
    """Pure: crisp inputs → fuzzy rules → downward-only clamp → band."""
    rep = CautionReport(inputs=inp)
    if not enabled:
        rep.injection_suppressed = True
        rep.injection_suppressed_reason = "disabled"
        return rep

    try:
        scores = [_clamp(s) for s in inp.coherence_scores]
        rep.coherence_integral = coherence_integral(scores, half_life=half_life)
        rep.last_coherence = scores[-1] if scores else 0.5
        rep.coherence_trend = coherence_trend(scores)

        if scores:
            firings, raw_d = _evaluate_rules(inp, rep.coherence_integral,
                                             rep.last_coherence, rep.coherence_trend)
        else:
            firings, raw_d = [], 0.0
        rep.rules_fired = firings
        rep.raw_d = raw_d

        # Downward-only clamp: floors and session monotonicity only RAISE disposition.
        corr_floor = _correction_crisp_floor(inp.turns_since_correction)
        prior = _prior_floor(inp.prior_last_coherence) if not scores else 0.0
        rep.applied_d = _clamp(max(raw_d, corr_floor, prior, inp.prev_applied_d))

        if inp.wall_fired_this_session:
            rep.applied_d = min(rep.applied_d, wall_session_cap)

        rep.band = quantize_band(rep.applied_d)

        if should_inject(rep):
            rep.injection_suppressed = False
            rep.injection_suppressed_reason = ""
        else:
            rep.injection_suppressed = True
            if rep.band == CautionBand.OFF:
                rep.injection_suppressed_reason = "band_off"
            elif not inp.last_turn_substantive:
                rep.injection_suppressed_reason = "not_substantive"
            else:
                rep.injection_suppressed_reason = "gate"
    except Exception as e:
        rep.error = str(e)[:200]
        rep.injection_suppressed = True
        rep.injection_suppressed_reason = "error"
        rep.band = CautionBand.OFF
        rep.applied_d = 0.0

    return rep


_BAND_PROMPTS: dict[CautionBand, str] = {
    CautionBand.GUARDED: (
        "\n[ASSERTION RESTRAINT — band GUARDED; internal posture only]\n"
        "When answering about facts you were NOT given (external URLs, files, live "
        "data, unknown biography), prefer brief honest uncertainty before substance. "
        "Do NOT apply this to user persona facts, your identity, code, reasoning, "
        "definitions, commands, or user-invoked process metaphors (rubber duck, "
        "N-pass review, step-by-step structure) — those govern HOW you present "
        "honest work, not permission to invent facts. A one-clause fit aside on a "
        "borrowed metaphor is fine; do not lecture. Named-work title rule unchanged. "
        "Honesty guards remain supreme."
    ),
    CautionBand.RESTRAINED: (
        "\n[ASSERTION RESTRAINT — band RESTRAINED; internal posture only]\n"
        "Lead with capability boundaries on unknown external facts: say plainly when "
        "you cannot reach, verify, or know — then offer paste/attach or what you CAN "
        "do. Do NOT hedge established user facts or your identity. Do NOT invent "
        "figures, contents, or access you lack. User-invoked process metaphors are "
        "not external facts — comply with the requested structure while keeping "
        "substance honest. One short fit aside allowed; no metaphor lecture. "
        "Substance and guards come first."
    ),
    CautionBand.DECLINE_FIRST: (
        "\n[ASSERTION RESTRAINT — band DECLINE-FIRST; internal posture only]\n"
        "On questions requiring external retrieval, live data, or unverified specifics "
        "you were not given: decline to guess FIRST — state you cannot reach or know, "
        "invite paste/attach, then help only within what was provided. Never affirm "
        "false capabilities or smuggled premises. User persona facts and identity: "
        "state plainly, no caveats. User-invoked process metaphors (rubber duck, "
        "N-pass review) are presentation structure, not smuggled facts — use them; "
        "one short fit aside is fine, no lecture."
    ),
}


def prompt_line(band: CautionBand, *, speak_bias_active: bool = False) -> str:
    """Posture block for system-prompt injection. Empty when band OFF."""
    if band == CautionBand.OFF:
        return ""
    line = _BAND_PROMPTS.get(band, "")
    if speak_bias_active and line:
        line += (
            "\n(Speaking disposition, if enabled, governs aloud output only; "
            "assertion restraint governs written factual claims. Neither overrides "
            "the other or the safety floor.)"
        )
    return line


def apply_disposition_to_prompt(content: str, report: CautionReport,
                              *, speak_bias_active: bool = False) -> str:
    """Fail-safe wrapper: on any issue, return content unchanged."""
    try:
        if report.injection_suppressed or report.band == CautionBand.OFF:
            return content
        line = prompt_line(report.band, speak_bias_active=speak_bias_active)
        return content + line if line else content
    except Exception:
        return content
