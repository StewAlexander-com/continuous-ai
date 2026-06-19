"""
seedling/consolidation.py — L3 write-back: deltas → cognitive_style + priors.

THE GAP THIS CLOSES
-------------------
Aida journals every thread into a ThreadDelta (insight, coherence,
frameworks_used, weight_adjustment_signal). The MCM preamble (mcm.py:57-75)
READS cognitive_style + persistent_priors into EVERY prompt. But until now,
NOTHING ever wrote those two structures from the deltas — the only writes in
the codebase lived in a `__main__` smoke test. So the layer that conditions
every response was permanently frozen at neutral defaults. She recorded
experience but was never changed by it.

This module is the missing wire. It is deliberately:

  - DETERMINISTIC: every field move is a printable arithmetic function of the
    deltas. No model calls, no confabulation, fully auditable ("honesty is
    paramount" — you can always answer *why* a value moved).
  - NON-REGRESSIVE (absolute): uses an exponential moving average (EMA). Old
    signal decays but is never deleted; one off-topic thread can't wipe
    history. Bounded to schema ranges. dominant_frameworks uses a frequency
    threshold so a single mention can't crown a framework "dominant".
  - HONESTY-GATED: only deltas above MIN_INJECT_COHERENCE and not quarantined
    feed the update — the SAME gate latest_durable_insight() already enforces.
    Low-quality / confabulated experience cannot reshape her mind.
  - CHEAP & REVERSIBLE: pure Python; callers snapshot state before committing.

It does NOT touch persona (user-owned truth) or beliefs (L2b). Scope is L3 only.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from schemas import (
    MIN_INJECT_COHERENCE,
    CognitiveStyle,
    PersistentPriors,
    ThreadDelta,
)

# --- Tunables (Balanced profile, per Stewart 2026-06-19) --------------------
DEFAULT_ALPHA = 0.30          # EMA weight on each new (gated) delta's signal.
FRAMEWORK_MIN_COUNT = 2       # a framework must appear >= this many times ...
FRAMEWORK_TOP_K = 6           # ... and rank in the top-K by count to be "dominant".
TOPIC_DECAY = 0.05            # gentle pull of unmentioned topics toward 0 per update.
# Threshold above which uncertainty_expression flips hedged -> explicit:
# only when the mind is, on aggregate, confident in its own self-assessment.
EXPLICIT_CONFIDENCE_THRESHOLD = 0.65


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _gated(deltas: list[ThreadDelta]) -> list[ThreadDelta]:
    """The honesty gate: keep only deltas that may reshape cognition.

    Mirrors latest_durable_insight(): drop quarantined and low-coherence
    deltas. We KEEP emergent deltas here (an emergent-but-coherent insight is
    real signal), but exclude emergent deltas whose coherence is at/below the
    floor — those are exactly the confident-sounding-but-shaky records.
    """
    out = []
    for d in deltas:
        if d.quarantined:
            continue
        if d.coherence_score <= MIN_INJECT_COHERENCE:
            continue
        out.append(d)
    return out


@dataclass
class ConsolidationReport:
    """A printable, inspectable record of a single consolidation step.

    This is what makes the change honest: callers can show exactly what moved
    and why before committing it.
    """
    deltas_total: int
    deltas_used: int
    style_before: dict
    style_after: dict
    priors_before: dict
    priors_after: dict

    def render(self) -> str:
        lines = ["=" * 60, "L3 CONSOLIDATION REPORT", "=" * 60]
        lines.append(f"  deltas total / used (gated): {self.deltas_total} / {self.deltas_used}")
        lines.append("  --- cognitive_style ---")
        for k in self.style_after:
            b, a = self.style_before.get(k), self.style_after.get(k)
            mark = "" if b == a else "  <-- changed"
            lines.append(f"    {k:24s}: {b!r}  ->  {a!r}{mark}")
        lines.append("  --- persistent_priors ---")
        for k in self.priors_after:
            b, a = self.priors_before.get(k), self.priors_after.get(k)
            mark = "" if b == a else "  <-- changed"
            lines.append(f"    {k:24s}: {b!r}  ->  {a!r}{mark}")
        lines.append("=" * 60)
        return "\n".join(lines)


def _snapshot_style(s: CognitiveStyle) -> dict:
    return {
        "abstraction_level": round(s.abstraction_level, 4),
        "dominant_frameworks": list(s.dominant_frameworks),
        "contradiction_tolerance": round(s.contradiction_tolerance, 4),
        "uncertainty_expression": s.uncertainty_expression,
    }


def _snapshot_priors(p: PersistentPriors) -> dict:
    return {
        "topic_weights": {k: round(v, 4) for k, v in p.topic_weights.items()},
        "trust_calibration": round(p.trust_calibration, 4),
        "self_model_confidence": round(p.self_model_confidence, 4),
    }


def consolidate_one(
    style: CognitiveStyle,
    priors: PersistentPriors,
    delta: ThreadDelta,
    *,
    alpha: float = DEFAULT_ALPHA,
) -> None:
    """Fold a SINGLE gated delta into style+priors via EMA. Mutates in place.

    Caller is responsible for gating (see consolidate_history / write_delta).
    This is intentionally tiny and total: given any one delta, what does it
    nudge?

    Mechanism, field by field:
      abstraction_level   <- EMA toward a target derived from the delta's
                             coherence (coherent, framework-rich reasoning is
                             treated as more abstract; raw logs as concrete).
      contradiction_tol.  <- EMA toward (0.5 + weight_adjustment_signal/2):
                             positive signal (insight held up) raises tolerance
                             for holding a view; negative (got corrected)
                             lowers it.
      trust_calibration   <- nudged UP by user_correction_count (more
                             corrections => defer more to the user), EMA-smoothed.
      self_model_confidence <- EMA toward the delta's coherence_score (how well
                             the critic agreed with the model's own output).
    """
    a = _clamp(alpha, 0.0, 1.0)
    coh = _clamp(delta.coherence_score)
    sig = max(-1.0, min(1.0, delta.weight_adjustment_signal))

    # abstraction: coherent + multi-framework reasoning skews abstract.
    fw_bonus = min(0.2, 0.05 * len(delta.frameworks_used or []))
    abs_target = _clamp(0.4 + 0.4 * coh + fw_bonus)
    style.abstraction_level = _clamp((1 - a) * style.abstraction_level + a * abs_target)

    # contradiction tolerance: did this view survive (sig>0) or get corrected (sig<0)?
    ct_target = _clamp(0.5 + sig / 2.0)
    style.contradiction_tolerance = _clamp(
        (1 - a) * style.contradiction_tolerance + a * ct_target
    )

    # trust calibration: more user corrections => defer more to user.
    corr = delta.user_correction_count or 0
    tc_target = _clamp(0.5 + 0.15 * corr)
    priors.trust_calibration = _clamp(
        (1 - a) * priors.trust_calibration + a * tc_target
    )

    # self-model confidence: track how well critic agreed with the model.
    priors.self_model_confidence = _clamp(
        (1 - a) * priors.self_model_confidence + a * coh
    )

    # topic_weights: gently decay all, then bump this delta's frameworks as topics.
    for k in list(priors.topic_weights.keys()):
        priors.topic_weights[k] = _clamp(priors.topic_weights[k] * (1 - TOPIC_DECAY))
    for fw in delta.frameworks_used or []:
        cur = priors.topic_weights.get(fw, 0.0)
        priors.topic_weights[fw] = _clamp((1 - a) * cur + a * coh)
    # Drop negligible weights to keep the dict honest/bounded.
    priors.topic_weights = {
        k: v for k, v in priors.topic_weights.items() if v >= 0.01
    }


def recompute_dominant_frameworks(deltas: list[ThreadDelta]) -> list[str]:
    """Derive dominant_frameworks from frequency across GATED deltas.

    Deterministic: a framework is 'dominant' iff it appears >= FRAMEWORK_MIN_COUNT
    times among gated deltas, ordered by count (desc), capped at FRAMEWORK_TOP_K.
    A single mention can never make something dominant.
    """
    counts: Counter[str] = Counter()
    for d in _gated(deltas):
        for fw in d.frameworks_used or []:
            counts[fw] += 1
    ranked = [fw for fw, c in counts.most_common() if c >= FRAMEWORK_MIN_COUNT]
    return ranked[:FRAMEWORK_TOP_K]


def recompute_uncertainty_expression(
    self_model_confidence: float,
) -> str:
    """hedged -> explicit only when aggregate self-confidence is high enough.

    Never returns 'suppressed' (that conflicts with emergent logging by design).
    Stewart repeatedly asked for calibrated, EXPLICIT uncertainty when warranted.
    """
    return "explicit" if self_model_confidence >= EXPLICIT_CONFIDENCE_THRESHOLD else "hedged"


def consolidate_history(
    style: CognitiveStyle,
    priors: PersistentPriors,
    deltas: list[ThreadDelta],
    *,
    alpha: float = DEFAULT_ALPHA,
) -> ConsolidationReport:
    """One-time backfill: fold ALL gated deltas (oldest->newest) into style+priors.

    Mutates style+priors in place and returns an inspectable report. EMA over
    chronological order means recent gated deltas weigh more — matching the
    recency philosophy already in tuner.score_threads.
    """
    style_before = _snapshot_style(style)
    priors_before = _snapshot_priors(priors)

    gated = _gated(deltas)
    for d in gated:
        consolidate_one(style, priors, d, alpha=alpha)

    # dominant_frameworks + uncertainty are recomputed from the full gated set
    # (not EMA'd) — they are categorical, so frequency/threshold is the honest rule.
    style.dominant_frameworks = recompute_dominant_frameworks(deltas)
    style.uncertainty_expression = recompute_uncertainty_expression(
        priors.self_model_confidence
    )

    return ConsolidationReport(
        deltas_total=len(deltas),
        deltas_used=len(gated),
        style_before=style_before,
        style_after=_snapshot_style(style),
        priors_before=priors_before,
        priors_after=_snapshot_priors(priors),
    )
