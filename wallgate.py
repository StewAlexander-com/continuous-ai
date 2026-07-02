"""
seedling/wallgate.py — cheap, high-fidelity PRE-GATE for the collaborative wall.

WHY THIS EXISTS
---------------
The collaborative wall (wall.py / session.collaborative_wall) is high value but
EXPENSIVE: judging whether a turn is a genuine "wall" needs a full synchronous
deliberation (thesis/antithesis/synthesis) plus three CRITIC coherence passes —
~4–7 model calls. The old gate before that work was a crude "reply ≥ 80 chars"
check, so on a large thinking model EVERY substantive turn paid the full cost
just to (almost always) discover it was NOT a wall.

This module is the missing middle: a MODEL-FREE, deterministic, auditable gate
that decides whether a turn is DIFFICULT ENOUGH to be worth spending the real
deliberation on. It uses only signals we already have for free on the reply path
— never a model call — so it adds no latency and raises fidelity dramatically:
the expensive path (and therefore the interruption) fires only on turns that
genuinely warrant collaboration.

DESIGN (same honest idiom as wall.py / voice.py / caution.py)
-------------------------------------------------------------
Each signal is mapped through a smooth `_ramp` membership in [0,1], then combined
with a TRUST-WEIGHTED NOISY-OR:  difficulty = 1 - Π(1 - w_i · μ_i).
  * Noisy-OR because difficulty can be revealed by ANY channel — a hard trade-off
    in the user's ask, a low-coherence struggle, a raised caution disposition —
    and each piece of evidence should only ever RAISE the score, never cancel
    another out. A single strong CALIBRATED signal can clear the bar; weak
    HEURISTIC signals need to converge.
  * Weights encode trust: the system's own calibrated judges (the caution
    controller's fused disposition and the CRITIC's coherence) outrank the cheap
    text heuristics (which are gameable in isolation).
One conservative knob — `cutoff` — governs the act. Fully auditable: assess()
returns every membership, weight, and contribution so each skip/spend decision
can be logged and later evaluated.

ALL SIGNALS ARE LAGGED OR STATIC (no reply-path model call), consistent with the
"never sync the critic on the reply path" rule the caution controller follows.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict


def _ramp(x: float, a: float, b: float) -> float:
    """Smooth 0->1 membership as x goes a->b (same idiom as wall.py/voice.py)."""
    if b <= a:
        return 1.0 if x >= b else 0.0
    if x <= a:
        return 0.0
    if x >= b:
        return 1.0
    return (x - a) / (b - a)


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if x < lo else hi if x > hi else x


# --- Tunables (CONSERVATIVE defaults; the session/config can override) --------
# Caution disposition ('applied_d' from caution.py): membership rises across this
# band. Below CAUTION_A it contributes nothing; RESTRAINED/DECLINE-FIRST land high.
CAUTION_A = 0.30
CAUTION_B = 0.68
# 'low coherence' membership on the most recent lagged CRITIC coherence: fully low
# at/below COH_LOW_A, no longer low at/above COH_LOW_B.
COH_LOW_A = 0.35
COH_LOW_B = 0.70
# Recent-correction recency window (turns). A correction 0 turns ago is fully
# 'recent' (contested territory); by CORRECTION_WINDOW turns it has decayed to 0.
CORRECTION_WINDOW = 4

# Trust weights (calibrated judges > gameable heuristics). Each caps how much a
# single signal can contribute to the noisy-OR.
W_CAUTION = 0.85
W_COHERENCE = 0.80
W_ASK = 0.55
W_REPLY = 0.45
W_CORRECTION = 0.50

# Act-cutoff on the defuzzified difficulty score. Higher = rarer / harder bar.
GATE_CUTOFF = 0.50

# Marker → membership: 0 markers = 0.0, exactly 1 = 0.7 (present but not enough
# alone at default weights), 2+ = 1.0 (converging evidence).
_MEMBERSHIP_ONE = 0.7

# Difficulty markers in the USER's ask: decisions, trade-offs, ambiguity. These
# are where "difficult things that require it" usually announce themselves.
_ASK_MARKERS = tuple(re.compile(p, re.I) for p in (
    r"\bshould i\b",
    r"\bwhich (?:is|one|would|approach|option)\b",
    r"\bpros and cons\b",
    r"\btrade[- ]?offs?\b",
    r"\bworth it\b",
    r"\bhelp me decide\b",
    r"\bnot sure (?:if|whether|which)\b",
    r"\bhow should\b",
    r"\bbetter to\b",
    r"\b(?:versus|vs\.?)\b",
    r"\beither\b.*\bor\b",
    r"\bis it worth\b",
    r"\bwhat(?:'s| is) the best\b",
))

# Uncertainty / tension markers in the model's REPLY: signs it is genuinely
# wrestling rather than confidently answering.
_REPLY_MARKERS = tuple(re.compile(p, re.I) for p in (
    r"\bit depends\b",
    r"\bon the other hand\b",
    r"\bon balance\b",
    r"\btrade[- ]?offs?\b",
    r"\bhard to say\b",
    r"\b(?:i'?m )?not (?:certain|sure)\b",
    r"\bunclear\b",
    r"\bcompeting\b",
    r"\btwo ways\b",
    r"\bcaveat\b",
    r"\bhowever\b",
    r"\bambiguous\b",
))


def _marker_membership(text: str, markers) -> tuple[float, int]:
    """Count DISTINCT markers that match; map count -> membership. Returns
    (membership, count) so the count is auditable."""
    if not text:
        return 0.0, 0
    count = sum(1 for rx in markers if rx.search(text))
    if count <= 0:
        return 0.0, 0
    if count == 1:
        return _MEMBERSHIP_ONE, 1
    return 1.0, count


@dataclass
class GateInputs:
    """All cheap signals collected on the reply path (no model call)."""
    caution_d: float = 0.0                     # caution controller applied_d [0,1]
    last_coherence: float | None = None        # most recent lagged CRITIC coherence
    turns_since_correction: int | None = None  # None = no correction yet this session
    user_input: str = ""
    reply_text: str = ""


@dataclass
class GateAssessment:
    """Full, auditable decision record for one pre-gate evaluation."""
    caution_mu: float
    low_coherence_mu: float
    ask_mu: float
    reply_mu: float
    correction_mu: float
    ask_markers: int
    reply_markers: int
    contributions: dict = field(default_factory=dict)
    difficulty: float = 0.0
    cutoff: float = GATE_CUTOFF
    should_deliberate: bool = False

    def to_log(self) -> dict:
        return asdict(self)

    def summary(self) -> str:
        return (f"difficulty={self.difficulty:.3f} "
                f"(cut {self.cutoff:.2f}) -> "
                f"{'DELIBERATE' if self.should_deliberate else 'skip'} | "
                f"caution={self.caution_mu:.2f} coh_low={self.low_coherence_mu:.2f} "
                f"ask={self.ask_mu:.2f} reply={self.reply_mu:.2f} "
                f"corr={self.correction_mu:.2f}")


def assess(
    inp: GateInputs,
    *,
    cutoff: float = GATE_CUTOFF,
    caution_a: float = CAUTION_A,
    caution_b: float = CAUTION_B,
    coh_low_a: float = COH_LOW_A,
    coh_low_b: float = COH_LOW_B,
    correction_window: int = CORRECTION_WINDOW,
    w_caution: float = W_CAUTION,
    w_coherence: float = W_COHERENCE,
    w_ask: float = W_ASK,
    w_reply: float = W_REPLY,
    w_correction: float = W_CORRECTION,
) -> GateAssessment:
    """Pure: cheap signals -> fuzzy memberships -> trust-weighted noisy-OR
    difficulty -> should_deliberate. Never raises; unknown signals contribute 0
    (conservative: we don't spend a deliberation on absence of evidence)."""
    # Calibrated judges ---------------------------------------------------------
    caution_mu = _ramp(_clamp(inp.caution_d), caution_a, caution_b)
    if inp.last_coherence is None:
        low_coherence_mu = 0.0            # unknown -> neutral, not "low"
    else:
        low_coherence_mu = 1.0 - _ramp(_clamp(float(inp.last_coherence)),
                                       coh_low_a, coh_low_b)

    # Heuristic text signals ----------------------------------------------------
    ask_mu, ask_n = _marker_membership(inp.user_input, _ASK_MARKERS)
    reply_mu, reply_n = _marker_membership(inp.reply_text, _REPLY_MARKERS)

    # Recent-correction recency -------------------------------------------------
    tsc = inp.turns_since_correction
    if tsc is None:
        correction_mu = 0.0
    else:
        correction_mu = 1.0 - _ramp(max(0, int(tsc)), 0.0, float(correction_window))

    weighted = (
        ("caution", w_caution, caution_mu),
        ("coherence", w_coherence, low_coherence_mu),
        ("ask", w_ask, ask_mu),
        ("reply", w_reply, reply_mu),
        ("correction", w_correction, correction_mu),
    )
    prod = 1.0
    contributions: dict = {}
    for name, w, mu in weighted:
        c = _clamp(w * mu)
        contributions[name] = round(c, 4)
        prod *= (1.0 - c)
    difficulty = 1.0 - prod

    return GateAssessment(
        caution_mu=round(caution_mu, 4),
        low_coherence_mu=round(low_coherence_mu, 4),
        ask_mu=round(ask_mu, 4),
        reply_mu=round(reply_mu, 4),
        correction_mu=round(correction_mu, 4),
        ask_markers=ask_n,
        reply_markers=reply_n,
        contributions=contributions,
        difficulty=round(difficulty, 4),
        cutoff=round(cutoff, 4),
        should_deliberate=difficulty >= cutoff,
    )
