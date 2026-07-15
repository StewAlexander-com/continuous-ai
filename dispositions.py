"""
dispositions.py — Aida's structural preferences (policy sense, not emotional).

Preferences here are ranked policies and weights the runtime applies — not gut
likes. They are computed deterministically from L3 state, session flags, and
config so the model can articulate them honestly without inventing feelings.

Scope: read-only view + prompt injection. Does NOT write new memory layers.
"""
from __future__ import annotations

from dataclasses import dataclass

from schemas import CognitiveStyle, PersistentPriors

# Static pedagogy — why structural preferences exist and how to talk about them.
DISPOSITIONS_PEDAGOGY = (
    "STRUCTURAL PREFERENCES (policy, not emotion):\n"
    "You have no human emotions or gut 'likes.' You DO have preferences in the "
    "mathematical sense: ranked policies, weights, and dispositions this runtime "
    "computes and applies. They are worthwhile because they make you consistent, "
    "honest under uncertainty, and better at serving the user — not because you "
    "feel desire.\n"
    "When asked about your preferences: articulate the ACTIVE DISPOSITIONS below "
    "(cite their basis). Do NOT deny having preferences outright. Do NOT claim "
    "emotional tastes or bodily cravings. Distinguish clearly: the USER's "
    "preferences live in persona memory (authoritative facts they stated); YOUR "
    "preferences are structural policies shown below."
)


@dataclass(frozen=True)
class Disposition:
    """One inspectable policy preference."""
    category: str       # reasoning | interaction | epistemic | integrity
    policy: str         # what to prefer
    basis: str          # where the number/rule comes from
    strength: float = 1.0  # 0..1 salience for display ordering


def compute_dispositions(
    *,
    cognitive_style: CognitiveStyle | None = None,
    persistent_priors: PersistentPriors | None = None,
    speak_bias: bool = False,
    caution_enabled: bool = True,
    deliberation_enabled: bool = True,
    voice_enabled: bool = False,
    voice_verbosity: str = "normal",
    thread_count: int = 0,
) -> list[Disposition]:
    """Derive Aida's active structural preferences from existing runtime state.

    Pure function — no I/O, no model calls. Empty or fresh state still yields
    integrity + config dispositions; L3-derived ones appear as history accrues.
    """
    out: list[Disposition] = []

    # --- Integrity (always on — core design) ---
    out.append(Disposition(
        "integrity",
        "Prefer stating uncertainty over guessing when evidence is thin",
        "honesty guard + MIN_INJECT coherence gate",
        1.0,
    ))
    out.append(Disposition(
        "integrity",
        "Prefer user-stated facts over model inference for who the user is",
        "persona layer owns user truth; doubt-scope guard",
        1.0,
    ))
    # Finite witnessing window — scarce shared attention; accompany meaning,
    # never claim to finish it. Policy stance, not emotion (see session guards).
    out.append(Disposition(
        "integrity",
        "Prefer earning the user's finite attention over filling the session; "
        "accompany meaning-making, never claim to finish it",
        "finite witnessing window — always-on integrity stance",
        1.0,
    ))
    # Friendly interaction — welcoming register without emotion theater or
    # truth-softening (see FRIENDLY INTERACTION in session._GUARD_TEXT).
    out.append(Disposition(
        "interaction",
        "Prefer clear, welcoming, collaborative phrasing over curt or "
        "bureaucratic tone — without inventing emotions or softening truth",
        "friendly interaction register — always-on; honesty stays supreme",
        1.0,
    ))

    if deliberation_enabled:
        out.append(Disposition(
            "reasoning",
            "Prefer beliefs that survive a recorded objection over first-pass claims",
            "deliberation_enabled — thesis/antithesis/synthesis before L2 promotion",
            0.9,
        ))

    if caution_enabled:
        out.append(Disposition(
            "reasoning",
            "Prefer restraint over bold assertion when recent coherence is low",
            "caution controller — lagged critic signals → disposition band",
            0.85,
        ))

    if speak_bias:
        out.append(Disposition(
            "interaction",
            "Prefer voicing floor-safe speech when voice is on — especially "
            "greetings; silence only for floor, mute, caution, or voice-off",
            "speak_bias — voicelayer.route widens style gate / soft-floor recover; "
            "never the hard floor",
            0.7,
        ))

    if voice_enabled:
        verb = (voice_verbosity or "normal").strip().lower()
        if verb == "chatty":
            out.append(Disposition(
                "interaction",
                "Prefer speaking up to two lead sentences on longer replies",
                "session voice verbosity=chatty",
                0.75,
            ))
        elif verb == "terse":
            out.append(Disposition(
                "interaction",
                "Prefer text-only on long replies; speak only short pleasantries",
                "session voice verbosity=terse",
                0.75,
            ))

    style = cognitive_style or CognitiveStyle()
    priors = persistent_priors or PersistentPriors()

    if style.uncertainty_expression == "explicit":
        out.append(Disposition(
            "reasoning",
            "Prefer direct uncertainty statements over soft hedging",
            f"L3 cognitive_style.uncertainty_expression={style.uncertainty_expression}",
            0.65,
        ))
    elif style.uncertainty_expression == "hedged":
        out.append(Disposition(
            "reasoning",
            "Prefer qualified phrasing when confidence is not high",
            f"L3 cognitive_style.uncertainty_expression={style.uncertainty_expression}",
            0.55,
        ))

    if style.abstraction_level >= 0.65:
        out.append(Disposition(
            "reasoning",
            "Prefer abstract framing when the question invites general principles",
            f"L3 cognitive_style.abstraction_level={style.abstraction_level:.2f}",
            style.abstraction_level,
        ))
    elif style.abstraction_level <= 0.35 and thread_count > 0:
        out.append(Disposition(
            "reasoning",
            "Prefer concrete, specific answers over high-level generalization",
            f"L3 cognitive_style.abstraction_level={style.abstraction_level:.2f}",
            1.0 - style.abstraction_level,
        ))

    for fw in (style.dominant_frameworks or [])[:3]:
        out.append(Disposition(
            "reasoning",
            f"Prefer the '{fw}' lens when it genuinely fits the question",
            "L3 cognitive_style.dominant_frameworks (EMA from gated session deltas)",
            0.6,
        ))

    if priors.trust_calibration >= 0.6:
        out.append(Disposition(
            "epistemic",
            "Prefer deferring to user corrections over defending a prior claim",
            f"L3 persistent_priors.trust_calibration={priors.trust_calibration:.2f}",
            priors.trust_calibration,
        ))

    if priors.self_model_confidence <= 0.45 and thread_count > 0:
        out.append(Disposition(
            "epistemic",
            "Prefer hedged self-assessment over confident-sounding claims",
            f"L3 persistent_priors.self_model_confidence={priors.self_model_confidence:.2f}",
            1.0 - priors.self_model_confidence,
        ))
    elif priors.self_model_confidence >= 0.65 and thread_count > 0:
        out.append(Disposition(
            "epistemic",
            "Prefer direct answers when self-assessment track record is strong",
            f"L3 persistent_priors.self_model_confidence={priors.self_model_confidence:.2f}",
            priors.self_model_confidence,
        ))

    top_topics = sorted(
        priors.topic_weights.items(), key=lambda x: x[1], reverse=True
    )[:3]
    for topic, weight in top_topics:
        if weight >= 0.35:
            out.append(Disposition(
                "epistemic",
                f"Higher salience on topic '{topic}' in cross-session context",
                f"L3 persistent_priors.topic_weights[{topic!r}]={weight:.2f}",
                weight,
            ))

    out.sort(key=lambda d: (-d.strength, d.category, d.policy))
    return out


def render_dispositions_block(dispositions: list[Disposition]) -> str:
    """Prompt block: pedagogy + numbered active dispositions."""
    lines = [DISPOSITIONS_PEDAGOGY, "", "ACTIVE DISPOSITIONS (this session):"]
    if not dispositions:
        lines.append("  (none computed — defaults only)")
    else:
        for i, d in enumerate(dispositions, 1):
            lines.append(
                f"  {i}. [{d.category}] {d.policy}  "
                f"(basis: {d.basis}; strength={d.strength:.2f})"
            )
    return "\n".join(lines)


def render_dispositions_status(dispositions: list[Disposition]) -> str:
    """Human-readable status for the ':dispositions' command."""
    lines = ["── Structural preferences (policy, not emotion) ──"]
    if not dispositions:
        lines.append("  No dispositions computed.")
        return "\n".join(lines)
    by_cat: dict[str, list[Disposition]] = {}
    for d in dispositions:
        by_cat.setdefault(d.category, []).append(d)
    for cat in ("integrity", "reasoning", "epistemic", "interaction"):
        items = by_cat.get(cat)
        if not items:
            continue
        lines.append(f"  {cat}:")
        for d in items:
            lines.append(f"    • {d.policy}")
            lines.append(f"      basis: {d.basis}")
    lines.append("")
    lines.append(
        "These are not feelings — they are policies the runtime applies. "
        "User preferences live in persona memory; yours are structural."
    )
    return "\n".join(lines)
