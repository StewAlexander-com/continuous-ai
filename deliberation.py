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
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("deliberation")


def _log_voice_failure(stage: str, exc: BaseException) -> None:
    """Token-cap discard is the designed fail-safe (never store fragments).
    That is INFO in the file log — not ERROR — so it stays off the chat TTY
    (console handler is WARNING+). Unexpected backend failures stay ERROR."""
    if getattr(exc, "expected_fail_safe", False):
        logger.info(f"{stage} discarded: {exc}")
    else:
        logger.error(f"{stage} failed: {exc}")


_LEDGER_DIR = Path("deliberation_ledger")
# Serialize ledger appends: live background deliberations (one daemon thread)
# and the end-of-session pass can both write. Append-only JSONL + this lock
# keeps records intact and non-interleaved.
_LEDGER_LOCK = threading.Lock()


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
    "soften. "
    # Doubt-scope rule: challenge only REASONING, never user-stated truth.
    "CRITICAL: challenge only the model's own REASONING or INFERENCE. NEVER object "
    "to or cast doubt on a fact the user stated about themselves \u2014 their name, "
    "location, job, preferences, or how they want the assistant to behave. The user "
    "is the sole authority on those; questioning whether they are true is out of "
    "scope, not insight. If the claim is purely such a user-stated fact, reply with "
    "exactly: NO SUBSTANTIVE OBJECTION. "
    "If \u2014 after genuine effort \u2014 there is truly no substantive objection, "
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


# Hard cap on re-challenge rounds. The whole point: depth scales with
# disagreement, but ALWAYS terminates so Aida is never stuck in a stalemate.
MAX_ROUNDS = 3            # absolute ceiling on antithesis<->synthesis rounds


def _objection_strength(antithesis: str) -> str:
    """Classify the strength of an objection from its text alone (no extra model
    call — the antithesis step already did the thinking). Returns one of:
    'none' | 'weak' | 'moderate' | 'strong'. Drives how many rounds we spend.

    Keyword heuristic by design (cheap). Negated strong markers ("not false",
    "cannot be wrong") are skipped so polarity flips don't inflate agreement
    pressure. Still noisy — see test_objection_strength_confusion_matrix.
    """
    a = antithesis.strip().lower()
    if "no substantive objection" in a or len(a) < 12:
        return "none"
    # weak: the objection itself hedges / concedes the claim mostly holds
    weak_markers = ("however", "but overall", "still valid", "still largely valid",
                    "minor", "mostly", "nitpick", "slight")
    if any(m in a for m in weak_markers):
        return "weak"
    # strong: hard contradiction language — but not when locally negated
    # ("this is not false, but…" must not map to strong).
    strong_markers = ("false", "wrong", "incorrect", "contradict", "fails", "cannot",
                      "never", "unsupported", "no evidence", "overgeneraliz")
    for m in strong_markers:
        idx = 0
        while True:
            pos = a.find(m, idx)
            if pos < 0:
                break
            before = a[max(0, pos - 8):pos]
            # Local negation / softener immediately before the marker.
            if not re.search(r"(?:\bnot\b|\bn't\b|no longer)\s*$", before):
                return "strong"
            idx = pos + len(m)
    return "moderate"


def _agreement_from_strength(strength: str) -> tuple[float, bool]:
    """Map objection strength -> (agreement, contested)."""
    return {
        "none":     (0.95, False),   # consensus = suspect, low information
        "weak":     (0.70, True),
        "moderate": (0.45, True),
        "strong":   (0.25, True),
    }[strength]


def _rounds_for(strength: str) -> int:
    """How many antithesis<->synthesis rounds this objection earns (always <=
    MAX_ROUNDS). Fast by default; deeper only when disagreement is real."""
    return {"none": 0, "weak": 1, "moderate": 1, "strong": min(2, MAX_ROUNDS)}[strength]


def deliberate(insight: str, thread_id: str, chat_fn, model: str) -> Deliberation:
    """Run an ADAPTIVE-DEPTH deliberation on a model-derived `insight`.

    chat_fn(model, messages) -> response_text  (injected so this stays testable
    and runtime-agnostic; session passes an ollama-backed callable).

    Depth is governed by disagreement, not the clock:
      * 1 model call when there is no real objection (early-exit on consensus).
      * 1 synthesis round for a weak/moderate objection.
      * up to 2 re-challenge rounds for a strong objection,
    and ALWAYS <= MAX_ROUNDS. The function is guaranteed to terminate and return
    a best synthesis, so Aida is never stuck in a stalemate.
    """
    thesis = insight.strip()
    ts = datetime.now(timezone.utc).isoformat()

    # 1) Antithesis: strongest objection to the original claim.
    try:
        antithesis = chat_fn(model, [
            {"role": "system", "content": _ANTITHESIS_SYS},
            {"role": "user", "content": f"Claim: {thesis}"},
        ]).strip()
    except Exception as e:
        _log_voice_failure("deliberation antithesis", e)
        # Fail safe: no deliberation possible -> pass the insight through unchanged.
        return Deliberation(thread_id, ts, thesis, "[deliberation unavailable]",
                            thesis, 1.0, False, note="error; passthrough")

    strength = _objection_strength(antithesis)
    agreement, contested = _agreement_from_strength(strength)
    budget = min(_rounds_for(strength), MAX_ROUNDS)

    # 2) EARLY EXIT: genuine consensus -> nothing to synthesize. One call total.
    if not contested or budget == 0:
        delib = Deliberation(
            thread_id, ts, thesis, antithesis, thesis, agreement, contested,
            note="uncontested (low-information consensus); early-exit, 1 call",
            voices=2, extra={"strength": strength, "rounds": 0},
        )
        _append_ledger(delib)
        return delib

    # 3) ADAPTIVE LOOP: synthesize, then re-challenge the synthesis. Depth scales
    #    with disagreement but is hard-capped. We always retain the best synthesis.
    synthesis = thesis
    current_objection = antithesis
    rounds_done = 0
    for i in range(budget):
        # Synthesis: reconcile the current objection.
        try:
            synthesis = chat_fn(model, [
                {"role": "system", "content": _SYNTHESIS_SYS},
                {"role": "user",
                 "content": f"Thesis: {thesis}\nAntithesis: {current_objection}"},
            ]).strip() or synthesis
        except Exception as e:
            _log_voice_failure(f"deliberation synthesis (round {i + 1})", e)
            break  # keep best synthesis so far; never stall
        rounds_done = i + 1

        # Stop if this was our last permitted round.
        if rounds_done >= budget:
            break
        # Re-challenge the synthesis: does a fresh objection survive?
        try:
            rechallenge = chat_fn(model, [
                {"role": "system", "content": _ANTITHESIS_SYS},
                {"role": "user", "content": f"Claim: {synthesis}"},
            ]).strip()
        except Exception as e:
            _log_voice_failure(f"deliberation re-challenge (round {i + 1})", e)
            break
        rs = _objection_strength(rechallenge)
        agreement, contested = _agreement_from_strength(rs)
        # Converged: synthesis now survives objection -> stop early.
        if rs == "none":
            break
        current_objection = rechallenge  # carry into the next round

    note = (f"contested ({strength}); {rounds_done} round(s), "
            "synthesis incorporates surviving objection")
    delib = Deliberation(
        thread_id, ts, thesis, current_objection, synthesis, agreement, contested,
        note=note, voices=3, extra={"strength": strength, "rounds": rounds_done},
    )
    _append_ledger(delib)
    return delib


def _append_ledger(d: Deliberation) -> None:
    """Append-only lineage log: the 'living history that withstands an audit'."""
    try:
        with _LEDGER_LOCK:
            _LEDGER_DIR.mkdir(exist_ok=True)
            path = _LEDGER_DIR / "ledger.jsonl"
            with open(path, "a") as f:
                f.write(json.dumps(d.to_dict()) + "\n")
    except Exception as e:
        logger.error(f"failed to append deliberation ledger: {e}")
