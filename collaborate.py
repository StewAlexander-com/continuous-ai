"""
seedling/collaborate.py — user-as-co-author at a deliberation wall.

When deliberation hits a WALL (weak candidate AND balanced opposition; see
wall.py), Aida surfaces her lean as a QUESTION and folds the user's answer back
into synthesis. The honest contract (settled with the user):

  - Agreement is a SIGNAL into synthesis, NEVER an auto-commit. The result still
    goes through the existing belief friction (coherence + contested/dissent).
  - User-assisted beliefs are promoted as REFLECTIONS carrying PROVENANCE: how
    they formed, what the user said, and \u2014 if the user's input was considered but
    NOT adopted \u2014 that overruled dissent is KEPT, never silently dropped.
  - She only PROBES a bare "agree" when the belief is still contested and the
    user's yes is the deciding weight (otherwise agreement is accepted as-is).
  - The collaborative question is about HER OWN reasoning (belief candidates),
    never about external facts \u2014 so it can't smuggle a confabulation past the
    guard as a leading question.

Everything here is pure and testable. The actual model calls + console I/O live
in the caller (seedling.py); this module decides WHAT to ask, HOW to fold a
response, and WHAT provenance to record \u2014 plus the auditable event record.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone

import wall


# ---------------------------------------------------------------------------
# 1) Should we open this deliberation to the user? (delegates to fuzzy wall)
# ---------------------------------------------------------------------------
def at_wall(coherence: float, thesis_score: float, antithesis_score: float,
            **kw) -> wall.WallAssessment:
    return wall.assess(coherence, thesis_score, antithesis_score, **kw)


# ---------------------------------------------------------------------------
# 2) Compose the collaborative QUESTION (about her reasoning, not a fact).
# ---------------------------------------------------------------------------
def compose_question(lean: str, because: str, pause: str) -> str:
    """A self-labeling interrogative: a question can't be mistaken for a fact
    claim, which is what makes speculation safe here. Kept compact + honest."""
    q = f"I'm leaning toward: {lean.strip()}"
    if because.strip():
        q += f"\n  \u2014 because {because.strip()}"
    if pause.strip():
        q += f"\n  \u2014 but this gives me pause: {pause.strip()}"
    q += "\n  Do you agree, or do you see it differently?"
    return q


# ---------------------------------------------------------------------------
# 3) Classify the user's response to the lean.
# ---------------------------------------------------------------------------
def classify_response(text: str) -> str:
    """-> 'agree' | 'counter' | 'ignore'. Conservative: only clear assent counts
    as agree; anything with substance is a counter; empty/topic-change is ignore.
    """
    if text is None:
        return "ignore"
    t = text.strip().lower()
    if not t:
        return "ignore"
    agree_words = {"yes", "agree", "i agree", "agreed", "yep", "yeah", "correct",
                   "right", "sounds right", "makes sense", "i think so", "sure",
                   "ok", "okay", "yes i agree", "that's right", "exactly"}
    if t.rstrip(".!") in agree_words:
        return "agree"
    # short bare negation with no reason -> treat as a (weak) counter, not agree
    return "counter"


def should_probe(response_kind: str, contested: bool) -> bool:
    """Probe a bare 'agree' ONLY when the belief is still contested and the yes
    is the deciding weight. Otherwise accept agreement as-is (never needy)."""
    return response_kind == "agree" and contested


# ---------------------------------------------------------------------------
# 4) Provenance for a collaboratively-formed reflection.
# ---------------------------------------------------------------------------
@dataclass
class CollabProvenance:
    formed_via: str = "collaborative"
    user_input: str = ""              # what the user actually said
    user_input_adopted: bool = True   # did it change/confirm the synthesis?
    overruled_dissent: str = ""       # user input considered but NOT adopted (kept!)
    derivation: str = ""              # short trace: thesis/antithesis/synthesis
    wall: dict = field(default_factory=dict)   # the WallAssessment that triggered it

    def to_dict(self) -> dict:
        return asdict(self)


def build_provenance(user_text: str, adopted: bool, derivation: str,
                     wall_assessment: wall.WallAssessment) -> CollabProvenance:
    """Record how a user-assisted belief formed. If the user's input was
    considered but NOT adopted, it is KEPT as overruled_dissent (the dissent-kept
    principle extended to user dissent) \u2014 never silently discarded."""
    p = CollabProvenance(
        user_input=user_text.strip(),
        user_input_adopted=adopted,
        derivation=derivation.strip(),
        wall=wall_assessment.to_log(),
    )
    if not adopted:
        p.overruled_dissent = user_text.strip()
    return p


# ---------------------------------------------------------------------------
# 5) Auditable wall-event log (the measurement hook).
# ---------------------------------------------------------------------------
def wall_event(assessment: wall.WallAssessment, lean: str, response_kind: str,
               synthesis_changed: bool, promoted: bool) -> dict:
    """The full decision tuple for one wall event \u2014 exactly what's needed to
    later answer 'does collaboration improve her beliefs?' and nothing more."""
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "wall": assessment.to_log(),
        "lean": lean.strip()[:200],
        "user_response": response_kind,        # agree | counter | ignore
        "synthesis_changed": bool(synthesis_changed),
        "promoted": bool(promoted),
    }
