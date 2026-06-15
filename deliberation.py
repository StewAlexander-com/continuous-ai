#!/usr/bin/env python3
"""Deliberated belief formation (3-voice, variance-gated).

The idea (stripped of metaphor to honest mechanism): a model-derived insight
should not enter durable memory just because one pass produced it. Instead, run
a small DELIBERATION — thesis, antithesis, synthesis — and PRESERVE the
disagreement rather than averaging it away. Consensus is treated as suspect
(low information); surviving a real objection is what earns confidence.

This is the engineering core behind the "committee that learns through friction"
framing. It is NOT "self-awareness" and there is no literal fractal geometry —
those are inspiration, not claims. What this actually does:

  thesis      = the candidate insight (as proposed)
  antithesis  = an agent whose ONLY job is the strongest objection / contradiction
  synthesis   = a reconciliation that must ACKNOWLEDGE the objection, not bury it

The output is the synthesized insight PLUS a recorded dissent and an agreement
signal, stored append-only as lineage. Anti-echo-chamber: if the antithesis
finds no real objection (genuine consensus), that is flagged as low-information
rather than celebrated, and the synthesis is kept conservative.

SCOPE GUARANTEE: this runs ONLY on model-derived insights. User-anchored facts
(directives, corrections) bypass deliberation entirely and remain verbatim —
the user still owns truth.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("deliberation")

_LEDGER_DIR = Path("deliberation_ledger")


@dataclass
class Deliberation:
    """The full lineage of one deliberated belief (append-only record)."""
    thread_id: str
    timestamp: str
    thesis: str                 # the candidate insight, as proposed
    antithesis: str             # the strongest objection found
    synthesis: str              # reconciled belief (acknowledges the objection)
    agreement: float            # 0..1; high = little real disagreement (suspect)
    contested: bool             # True if a substantive objection survived
    note: str = ""
    voices: int = 3
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "thread_id": self.thread_id, "timestamp": self.timestamp,
            "thesis": self.thesis, "antithesis": self.antithesis,
            "synthesis": self.synthesis, "agreement": round(self.agreement, 3),
            "contested": self.contested, "note": self.note, "voices": self.voices,
            **({"extra": self.extra} if self.extra else {}),
        }


# --- prompts: deliberately DIVERGENT roles so the friction is real ---
_ANTITHESIS_SYS = (
    "You are the Antithesis voice in a deliberation. Your ONLY job is to find the "
    "single strongest, most specific objection to the claim below: where is it "
    "false, overstated, unsupported, or context-dependent? Do NOT agree, hedge, or "
    "soften. If \u2014 after genuine effort \u2014 there is truly no substantive objection, "
    "reply with exactly: NO SUBSTANTIVE OBJECTION. Otherwise give one sharp objection "
    "in 1\u20132 sentences."
)
_SYNTHESIS_SYS = (
    "You are the Synthesis voice. You are given a claim (thesis) and the strongest "
    "objection to it (antithesis). Produce a single revised belief that EXPLICITLY "
    "accounts for the objection \u2014 narrow the claim, add the condition, or correct it. "
    "Do not ignore or bury the objection. If the objection fully defeats the claim, "
    "say so plainly. Reply with one concise sentence: the revised belief."
)


def _agreement_from_objection(antithesis: str) -> tuple[float, bool]:
    """Map the antithesis text to an agreement score. 'NO SUBSTANTIVE OBJECTION'
    => high agreement / not contested (suspect: low information). A real
    objection => lower agreement / contested (the valuable case)."""
    a = antithesis.strip().lower()
    if "no substantive objection" in a or len(a) < 12:
        return 0.95, False           # consensus = suspect, low information
    # crude proxy: longer, hedge-free objections signal more genuine contest
    hedges = sum(w in a for w in ("however", "but overall", "still valid", "minor"))
    base = 0.45 if hedges else 0.30
    return base, True


def deliberate(insight: str, thread_id: str, chat_fn, model: str) -> Deliberation:
    """Run a 3-voice deliberation on a model-derived `insight`.

    chat_fn(model, messages) -> response_text  (injected so this stays testable
    and runtime-agnostic; session passes an ollama-backed callable).
    """
    thesis = insight.strip()
    ts = datetime.now(timezone.utc).isoformat()

    # 1) Antithesis: strongest objection.
    try:
        antithesis = chat_fn(model, [
            {"role": "system", "content": _ANTITHESIS_SYS},
            {"role": "user", "content": f"Claim: {thesis}"},
        ]).strip()
    except Exception as e:
        logger.error(f"deliberation antithesis failed: {e}")
        # Fail safe: no deliberation possible -> pass the insight through unchanged.
        return Deliberation(thread_id, ts, thesis, "[deliberation unavailable]",
                            thesis, 1.0, False, note="error; passthrough")

    agreement, contested = _agreement_from_objection(antithesis)

    # 2) Synthesis: reconcile (only meaningful if contested; else keep thesis).
    if contested:
        try:
            synthesis = chat_fn(model, [
                {"role": "system", "content": _SYNTHESIS_SYS},
                {"role": "user", "content": f"Thesis: {thesis}\nAntithesis: {antithesis}"},
            ]).strip()
        except Exception as e:
            logger.error(f"deliberation synthesis failed: {e}")
            synthesis = thesis
    else:
        synthesis = thesis  # uncontested -> nothing to synthesize; keep, flag low-info

    note = ("uncontested (low-information consensus)" if not contested
            else "contested; synthesis incorporates the objection")
    delib = Deliberation(thread_id, ts, thesis, antithesis, synthesis,
                         agreement, contested, note=note, voices=3)
    _append_ledger(delib)
    return delib


def _append_ledger(d: Deliberation) -> None:
    """Append-only lineage log: the 'living history that withstands an audit'."""
    try:
        _LEDGER_DIR.mkdir(exist_ok=True)
        path = _LEDGER_DIR / "ledger.jsonl"
        with open(path, "a") as f:
            f.write(json.dumps(d.to_dict()) + "\n")
    except Exception as e:
        logger.error(f"failed to append deliberation ledger: {e}")
