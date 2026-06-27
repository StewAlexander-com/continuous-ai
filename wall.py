"""
seedling/wall.py — fuzzy "deliberation wall" detection for collaborative reasoning.

THE IDEA
--------
Aida's deliberation runs thesis -> antithesis -> synthesis silently. Most of the
time synthesis resolves on its own. But sometimes it hits a WALL: the candidate
synthesis is weak AND the two opposing positions are roughly balanced — she's
genuinely stuck, not 80%-confident-and-insecure. ONLY at a wall is it worth
turning to the user ("I'm leaning X, because Y; Z gives me pause — do you
agree?") and folding their answer back into synthesis. This keeps the
collaborative interruption RARE and high-value (the ~20% where the user's input
actually changes the outcome), never needy.

WHY FUZZY (not a hard threshold)
--------------------------------
A brittle `coherence < 0.6 AND margin < 0.1` cliff is gameable and arbitrary.
Instead we map both signals through smooth membership curves (the same honest
`_ramp` idiom as voice.py), AND them fuzzily, and defuzzify to a wall_score in
[0,1]. Conservatism is then set by ONE clear knob: the act-cutoff on wall_score
(default high, so she asks too rarely rather than too often). Fuzzy shapes the
*score*; the conservative cutoff governs the *act*. Those two requirements
(fuzzy + conservative) are complementary, not in tension.

ALL INPUTS ARE FROM THE CRITIC — never model self-report (a small model can't
reliably self-rate confidence; that would reintroduce the very miscalibration
we're guarding against).

AUDITABLE: assess() returns the full decision tuple so every wall event can be
logged and later evaluated ("does collaboration improve her beliefs?").
"""
from __future__ import annotations

from dataclasses import dataclass, asdict


def _ramp(x: float, a: float, b: float) -> float:
    """Smooth 0->1 membership as x goes a->b. Below a => 0, above b => 1, linear
    between. The honest 'fuzzy' curve — same idiom as voice.py (no brittle step)."""
    if b <= a:
        return 1.0 if x >= b else 0.0
    if x <= a:
        return 0.0
    if x >= b:
        return 1.0
    return (x - a) / (b - a)


# --- Tunables (CONSERVATIVE defaults; config-overridable). --------------------
# "low coherence" membership: fully 'low' at/below LOW_A, not low at/above LOW_B.
COHERENCE_LOW_A = 0.30
COHERENCE_LOW_B = 0.65
# "balanced opposition" membership on |thesis - antithesis|: fully balanced at 0,
# not balanced once the gap reaches MARGIN_B.
MARGIN_BALANCED_B = 0.30
# Act-cutoff on the defuzzified wall_score. HIGH = conservative (ask rarely).
WALL_ACT_CUTOFF = 0.70


@dataclass
class WallAssessment:
    """The full, auditable decision record for one deliberation."""
    coherence: float          # critic coherence of the candidate synthesis
    thesis_score: float       # critic coherence of the thesis position
    antithesis_score: float   # critic coherence of the antithesis position
    margin: float             # |thesis - antithesis| (0 = perfectly balanced)
    low_coherence_mu: float   # fuzzy membership: how 'low' is coherence
    balanced_mu: float        # fuzzy membership: how 'balanced' is the opposition
    wall_score: float         # defuzzified AND of the two memberships
    cutoff: float             # the conservative act-cutoff in force
    is_wall: bool             # wall_score >= cutoff -> surface to the user

    def to_log(self) -> dict:
        return asdict(self)


def assess(
    coherence: float,
    thesis_score: float,
    antithesis_score: float,
    *,
    cutoff: float = WALL_ACT_CUTOFF,
    low_a: float = COHERENCE_LOW_A,
    low_b: float = COHERENCE_LOW_B,
    margin_b: float = MARGIN_BALANCED_B,
) -> WallAssessment:
    """Pure: critic numbers -> fuzzy memberships -> wall_score -> is_wall.

    A WALL needs BOTH: the candidate is weakly coherent AND the two positions
    are balanced (neither clearly wins). Fuzzy-AND via product so neither alone
    triggers — a low-but-decisive deliberation, or a balanced-but-confident one,
    won't cross the line.
    """
    coherence = max(0.0, min(1.0, coherence))
    thesis_score = max(0.0, min(1.0, thesis_score))
    antithesis_score = max(0.0, min(1.0, antithesis_score))
    margin = abs(thesis_score - antithesis_score)

    # 'low coherence' membership = 1 - ramp(coherence): full at low coherence,
    # fading to 0 as coherence rises through [low_a, low_b].
    low_coherence_mu = 1.0 - _ramp(coherence, low_a, low_b)
    # 'balanced' membership = 1 - ramp(margin): full at margin 0, fading to 0 as
    # the gap between the positions widens to margin_b.
    balanced_mu = 1.0 - _ramp(margin, 0.0, margin_b)

    wall_score = low_coherence_mu * balanced_mu     # fuzzy AND (product)
    is_wall = wall_score >= cutoff

    return WallAssessment(
        coherence=round(coherence, 4),
        thesis_score=round(thesis_score, 4),
        antithesis_score=round(antithesis_score, 4),
        margin=round(margin, 4),
        low_coherence_mu=round(low_coherence_mu, 4),
        balanced_mu=round(balanced_mu, 4),
        wall_score=round(wall_score, 4),
        cutoff=round(cutoff, 4),
        is_wall=is_wall,
    )
