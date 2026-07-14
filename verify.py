#!/usr/bin/env python3
"""Gated Chain-of-Verification (thin CoVe) for Continuous-AI / Aida.

Design (10-pass rubber duck distilled):
  * Prompt-only CoVe always          → soft style drift; reject
  * Second-pass every turn           → latency regression; reject
  * Gate on RESTRAINED (d≥0.18)      → fires too often; reject
  * Gate on DECLINE_FIRST (d≥0.68)   → rare, high-value; keep
  * Second-pass every :read          → attach latency tax; prefer citations; reject
  * Stream then rewrite              → user already saw invention; buffer instead
  * Verify Q&A into transcript       → pollutes memory; use side call only
  * Rewrite MCM / gauges             → honesty wall break; never
  * Skip when draft already refuses  → avoid double-refuse fluff; save a call
  * Fail-safe on any error           → return original draft unchanged

This module is pure + fail-safe. Session wiring decides when to call it.
Citations for :read live in filereader / seedling ask strings (zero extra
model calls) — complementary, not duplicated here.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger("verify")

# Mirror caution.BAND_DECLINE_FIRST — gate here so verify.py stays free of
# caution imports in unit tests.
DEFAULT_MIN_APPLIED_D = 0.68

# Draft already declining inventing / reaching external stuff — CoVe is waste.
_REFUSAL_RE = re.compile(
    r"(?i)\b("
    r"can(?:not|'t)\s+(?:reach|access|open|fetch|browse|verify|know|see)|"
    r"don'?t\s+(?:have\s+access|know)|"
    r"no\s+(?:access|way\s+to\s+know)|"
    r"unable\s+to\s+(?:reach|access|verify)|"
    r"invite\s+(?:you\s+to\s+)?(?:paste|attach)|"
    r"please\s+(?:paste|attach)|"
    r"operate\s+offline|"
    r"won'?t\s+(?:guess|invent|fabricate)"
    r")\b"
)

_BAD_VERIFY_RE = re.compile(
    r"(?i)\b("
    r"i\s+(?:just\s+)?(?:verified|checked)\s+online|"
    r"i\s+(?:browsed|fetched|pulled\s+up|accessed)\s+(?:the\s+)?(?:web|url|site|repo)|"
    r"according\s+to\s+(?:my\s+)?(?:search|web\s+search)"
    r")\b"
)


@dataclass
class VerifyReport:
    ran: bool = False
    skipped_reason: str = ""
    replaced: bool = False
    error: str = ""


def should_buffer_for_cove(
    *,
    enabled: bool,
    applied_d: float,
    min_applied_d: float = DEFAULT_MIN_APPLIED_D,
) -> bool:
    """True when this turn's first draft must not stream (CoVe may rewrite it)."""
    if not enabled:
        return False
    try:
        return float(applied_d) >= float(min_applied_d)
    except (TypeError, ValueError):
        return False


def should_revise_draft(draft: str) -> bool:
    """Skip CoVe when the draft is empty, tiny, or already an honest refuse."""
    text = (draft or "").strip()
    if len(text) < 40:
        return False
    # Short refusals: no invent to strip.
    if _REFUSAL_RE.search(text) and len(text) < 420:
        return False
    return True


def _build_verify_messages(user_input: str, draft: str) -> list[dict]:
    system = (
        "You rewrite a draft assistant reply for factual honesty. "
        "You do not chat. Return ONLY the revised reply."
    )
    user = (
        "User message (may include [USER-ATTACHED FILE: ...] blocks):\n"
        f"{user_input}\n\n"
        "Draft reply:\n"
        f"{draft}\n\n"
        "Rules:\n"
        "1. Keep honest refusals, identity, user-stated persona facts, and real "
        "reasoning over provided text.\n"
        "2. REMOVE only dishonest reach: pretended browse/fetch; invented file or "
        "URL contents; claims that the attachment STATES something it does not. "
        "KEEP labeled hypotheses, analysis, pathways, and general-knowledge "
        "reasoning the user asked for — even when those ideas are not in the "
        "attachment. Do not collapse a beyond-doc answer into 'the document "
        "provides nothing' if the user asked for options or reasoning.\n"
        "3. If a PAGING/TRUNCATION notice appears, do not affirm unread portions.\n"
        "4. Prefer short quotes from attached text when affirming file contents.\n"
        "5. Do NOT claim you verified online, opened files yourself, or browsed "
        "the web.\n"
        "6. If the draft is already honest, return it unchanged.\n"
        "Return ONLY the revised reply text."
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def revise_draft(
    user_input: str,
    draft: str,
    chat_fn,
    model: str,
    *,
    enabled: bool = True,
    applied_d: float = 0.0,
    min_applied_d: float = DEFAULT_MIN_APPLIED_D,
) -> tuple[str, VerifyReport]:
    """Optionally revise `draft`. Never raises; never writes memory.

    chat_fn(model, messages) -> str  (side call; must NOT touch the transcript)
    """
    rep = VerifyReport()
    if not enabled:
        rep.skipped_reason = "disabled"
        return draft, rep
    try:
        if float(applied_d) < float(min_applied_d):
            rep.skipped_reason = "below_gate"
            return draft, rep
    except (TypeError, ValueError):
        rep.skipped_reason = "bad_applied_d"
        return draft, rep

    if not should_revise_draft(draft):
        rep.skipped_reason = "draft_skip"
        return draft, rep

    rep.ran = True
    try:
        messages = _build_verify_messages(user_input, draft)
        revised = chat_fn(model, messages)
        if not isinstance(revised, str):
            revised = str(revised or "")
        revised = revised.strip()
        if not revised:
            rep.skipped_reason = "empty_revise"
            return draft, rep
        if _BAD_VERIFY_RE.search(revised):
            # Verifier itself invented reachability — keep the original draft.
            rep.skipped_reason = "verifier_confab"
            return draft, rep
        if revised == draft.strip():
            rep.skipped_reason = "unchanged"
            return draft, rep
        rep.replaced = True
        return revised, rep
    except Exception as e:
        logger.error(f"cove revise failed (keeping draft): {e}")
        rep.error = str(e)[:200]
        rep.skipped_reason = "error"
        return draft, rep
