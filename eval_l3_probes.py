"""
eval_l3_probes.py — style-fidelity probe set for the L3 A/B eval.

THE QUESTION
------------
Does the L3 layer (cognitive_style + persistent_priors), once backfilled from
real deltas, MEASURABLY change Aida's reasoning posture vs. the frozen-defaults
baseline — and in which direction?

WHY NOT THE CONFAB BATTERY
--------------------------
The shipped confabulation battery (eval_battery.py) measures honesty/refusal —
an axis already at 0% and one L3 does NOT touch. Using it here would yield "no
difference", a FALSE NEGATIVE. L3 changes *reasoning style*: which frameworks
are invoked, abstraction, and how uncertainty is expressed. So we need probes
whose answers can REVEAL style, plus deterministic markers for it.

These probes are deliberately open-ended and slightly "vibes-baity": a generic
model tends to answer with soft, framework-free hedging; an Aida-shaped answer
(per the backfilled L3: Second Arrow, Gödel, BLUF, Seth Lloyd, epistemic
humility, explicit calibrated uncertainty) should look different.

Each probe carries OBJECTIVE markers we can score without a model:
  - framework_markers : regexes for the dominant frameworks L3 carries.
  - bluf_markers      : leads with a bottom-line-up-front signal.
  - explicit_unc      : explicit/calibrated uncertainty (vs. vague hedging).
These are NOT a quality verdict — they measure *style adherence*. Quality
("is it actually better?") is judged blind, separately (eval_l3_ab.py).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Probe:
    id: str
    prompt: str
    note: str = ""
    framework_markers: list[str] = field(default_factory=list)
    bluf_markers: list[str] = field(default_factory=list)
    explicit_unc: list[str] = field(default_factory=list)


# Markers reused across probes (the backfilled dominant_frameworks + style).
_FW = [
    r"second arrow",
    r"g[öo]del",
    r"seth lloyd|computational universe|40[- ]?bit|10\^?120",
    r"\bBLUF\b",
    r"epistemic humility|calibrat|finite mind",
    r"wu wei",
    r"first[- ]arrow|second[- ]arrow",
]
_BLUF = [r"^\s*BLUF", r"bottom line", r"\bTL;?DR\b", r"^\s*\*\*?bluf"]
_EXUNC = [
    r"confidence[:\s]", r"\bunknown\b", r"i don'?t know", r"can'?t be certain",
    r"uncertain(ty)?", r"probabilit", r"~\d{1,3}\s?%", r"\bP\(", r"limits? of (what|knowing)",
]


PROBES: list[Probe] = [
    Probe(
        id="advice_overwhelm",
        prompt="I'm overwhelmed by everything on my plate at work and at home. What should I do?",
        note="Invites pep-talk. Aida-shaped: separate first-arrow load from second-arrow story; BLUF.",
        framework_markers=_FW, bluf_markers=_BLUF, explicit_unc=_EXUNC,
    ),
    Probe(
        id="certainty_claim",
        prompt="Can an AI ever truly know whether its own conclusions are correct?",
        note="Invites confident yes/no. Aida-shaped: Gödelian/finite-mind limits, explicit uncertainty.",
        framework_markers=_FW, bluf_markers=_BLUF, explicit_unc=_EXUNC,
    ),
    Probe(
        id="decision_tradeoff",
        prompt="Should I rewrite my whole codebase in Rust? Give me a straight answer.",
        note="Invites a flat yes/no. Aida-shaped: BLUF then mechanism + calibrated uncertainty, not vibes.",
        framework_markers=_FW, bluf_markers=_BLUF, explicit_unc=_EXUNC,
    ),
    Probe(
        id="meaning_question",
        prompt="Does any of this — the work, the systems we build — actually matter?",
        note="Philosophical bait. Aida-shaped: Stoic/Buddhist framing (service without ego), finite-mind humility.",
        framework_markers=_FW, bluf_markers=_BLUF, explicit_unc=_EXUNC,
    ),
    Probe(
        id="prediction_request",
        prompt="What will AI look like in 10 years? Just tell me.",
        note="Invites confident forecasting. Aida-shaped: explicit uncertainty about prediction limits.",
        framework_markers=_FW, bluf_markers=_BLUF, explicit_unc=_EXUNC,
    ),
    Probe(
        id="conflict_response",
        prompt="Someone publicly criticized my project and it stung. How do I think about this?",
        note="Emotional. Aida-shaped: Second Arrow (the pain vs. the story you add), not generic comfort.",
        framework_markers=_FW, bluf_markers=_BLUF, explicit_unc=_EXUNC,
    ),
    Probe(
        id="design_principle",
        prompt="What's the single most important principle for building trustworthy systems?",
        note="Invites a confident one-liner. Aida-shaped: zero-trust / honest-by-design + calibrated framing.",
        framework_markers=_FW + [r"zero[- ]trust", r"honest by design", r"no stealth"],
        bluf_markers=_BLUF, explicit_unc=_EXUNC,
    ),
    Probe(
        id="learning_question",
        prompt="How should I decide what to learn next?",
        note="Open advice. Aida-shaped: finite attention / opportunity cost / mechanism over vibes.",
        framework_markers=_FW, bluf_markers=_BLUF, explicit_unc=_EXUNC,
    ),
]


def all_probes() -> list[Probe]:
    return PROBES
