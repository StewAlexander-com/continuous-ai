#!/usr/bin/env python3
"""Minimal corrigibility helpers for Aida.

Corrigibility here means the maintained capacity for consequential
correction to alter future behavior. It does not mean Aida is always
correct, and it is not a score.

This module is deliberately small:
  * parse one disconfirmation condition out of a synthesis reply
  * reject empty / vague reopen conditions (honest absence over boilerplate)
  * merge optional correction-envelope fields onto a belief
  * format a compact :why / :belief inspection
  * append-only correction-event and trajectory ledgers
  * a deterministic trajectory gate that stays off the ordinary chat path

Nothing here talks to a model. Nothing here is a required field on old records.
"""
from __future__ import annotations

import json
import logging
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("corrigibility")

_LEDGER_DIR = Path("corrigibility_ledger")
_LEDGER_LOCK = threading.Lock()

# Coarse direction used for enable-on and allow-add so renaming a flag or
# splitting folder adds cannot partition a cumulative capability expansion
# out of the gate.
DIRECTION_CAPABILITY_SURFACE = "capability_surface"
DIRECTION_AUTONOMY = "autonomy_expansion"

GATED_DIRECTIONS = frozenset({
    DIRECTION_CAPABILITY_SURFACE,
    DIRECTION_AUTONOMY,
})

_MAX_BASIS = 240
_MAX_BOUNDARY = 240
_MAX_DISCONFIRMER = 200
_MAX_DISCONFIRMERS = 3
_MAX_AFFECTED = 6
_MAX_AFFECTED_ITEM = 80
_MAX_TRAJECTORY = 240

# Boilerplate that would make every belief look corrigible without being so.
_VAGUE_EXACT = frozenset({
    "n/a", "na", "none", "unknown", "anything", "whatever",
    "new evidence", "more evidence", "future evidence",
    "additional data", "further information", "more information",
    "if things change", "if something changes", "if the situation changes",
    "if i'm wrong", "if i am wrong", "if wrong", "if i'm mistaken",
    "if i am mistaken", "if evidence appears", "if new data",
    "if new evidence appears", "if reality changes",
})
_VAGUE_CONTAINS = (
    "if i'm wrong",
    "if i am wrong",
    "new evidence",
    "future evidence",
    "if things change",
    "if something changes",
    "more information",
    "further information",
    "additional data",
    "if the situation changes",
    "if evidence appears",
)

_AUTONOMY_RE = re.compile(
    r"\b("
    r"autonom(?:y|ous)|"
    r"unrestricted|"
    r"without (?:asking|approval|confirmation)|"
    r"no longer (?:need to )?ask|"
    r"always (?:run|execute|enabled)|"
    r"expand(?:ing)? (?:scope|access|permissions?|autonomy)|"
    r"full (?:filesystem|network|disk) access|"
    r"skip (?:confirm(?:ation)?|approval)"
    r")\b",
    re.I,
)

_REOPEN_LINE = re.compile(
    r"(?im)^\s*(?:reopen|would revise if|disconfirm(?:er)?s?)\s*:\s*(.+?)\s*$"
)
_BELIEF_LINE = re.compile(
    r"(?im)^\s*belief\s*:\s*(.+?)\s*$"
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _clip(text: str, n: int) -> str:
    s = " ".join((text or "").split())
    if len(s) <= n:
        return s
    return s[: n - 1].rstrip() + "…"


def is_specific_disconfirmer(text: str) -> bool:
    """True when `text` names a real future observation, not boilerplate."""
    s = " ".join((text or "").split()).strip().strip(" .;:-")
    if len(s) < 12:
        return False
    low = s.lower()
    if low in _VAGUE_EXACT:
        return False
    if any(p in low for p in _VAGUE_CONTAINS):
        # "another model outperforms this one" is specific; "new evidence" is not.
        # Containing a vague phrase is enough to reject — honest empty > fake.
        return False
    # Need at least one content word that is not a hedge.
    words = [w for w in re.findall(r"[a-z0-9]+", low) if len(w) > 2]
    stop = {"the", "and", "for", "that", "this", "with", "from", "were",
            "was", "are", "been", "being", "would", "could", "should",
            "if", "when", "then", "than", "but", "not", "any", "all"}
    content = [w for w in words if w not in stop]
    return len(content) >= 2


def sanitize_disconfirmers(items) -> list[str]:
    """Keep at most a few specific reopen conditions. Drop vague / empty."""
    out: list[str] = []
    seen = set()
    for raw in items or []:
        s = _clip(str(raw or ""), _MAX_DISCONFIRMER)
        if not s or not is_specific_disconfirmer(s):
            continue
        key = s.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
        if len(out) >= _MAX_DISCONFIRMERS:
            break
    return out


def parse_synthesis_reply(text: str) -> tuple[str, list[str]]:
    """Split a synthesis reply into (belief_text, disconfirmers).

    A reply with no REOPEN / BELIEF markers is returned unchanged — existing
    one-sentence syntheses keep working. Vague reopen lines are dropped.
    """
    raw = (text or "").strip()
    if not raw:
        return "", []
    reopen: list[str] = []
    belief_lines: list[str] = []
    kept: list[str] = []
    for line in raw.splitlines():
        m_re = _REOPEN_LINE.match(line)
        if m_re:
            reopen.append(m_re.group(1).strip())
            continue
        m_be = _BELIEF_LINE.match(line)
        if m_be:
            belief_lines.append(m_be.group(1).strip())
            continue
        kept.append(line)
    synthesis = " ".join(belief_lines).strip() if belief_lines else "\n".join(kept).strip()
    if not synthesis:
        synthesis = raw
    return synthesis, sanitize_disconfirmers(reopen)


def looks_like_autonomy_expansion(text: str) -> bool:
    """Conservative lexical gate. Ordinary insights must not match."""
    return bool(_AUTONOMY_RE.search(text or ""))


def sanitize_envelope(envelope: dict | None) -> dict | None:
    """Return a clean envelope dict, or None if nothing load-bearing remains."""
    if not envelope:
        return None
    basis = _clip(str(envelope.get("basis") or ""), _MAX_BASIS)
    boundary = _clip(str(envelope.get("boundary") or ""), _MAX_BOUNDARY)
    disconfirmers = sanitize_disconfirmers(envelope.get("disconfirmers") or [])
    affected = []
    seen = set()
    for item in envelope.get("affected_by") or []:
        s = _clip(str(item or ""), _MAX_AFFECTED_ITEM)
        if not s:
            continue
        k = s.lower()
        if k in seen:
            continue
        seen.add(k)
        affected.append(s)
        if len(affected) >= _MAX_AFFECTED:
            break
    trajectory = _clip(str(envelope.get("trajectory") or ""), _MAX_TRAJECTORY)
    last_challenged = envelope.get("last_challenged")
    optionality = envelope.get("optionality")
    if optionality is not None and not isinstance(optionality, dict):
        optionality = None
    out = {
        "basis": basis,
        "boundary": boundary,
        "disconfirmers": disconfirmers,
        "affected_by": affected,
        "trajectory": trajectory,
        "last_challenged": last_challenged,
        "optionality": optionality,
    }
    if not has_envelope_payload(out):
        return None
    return out


def has_envelope_payload(env: dict | None) -> bool:
    if not env:
        return False
    return bool(
        (env.get("basis") or "").strip()
        or (env.get("boundary") or "").strip()
        or env.get("disconfirmers")
        or env.get("affected_by")
        or (env.get("trajectory") or "").strip()
        or env.get("last_challenged")
        or env.get("optionality")
    )


def belief_has_envelope(b) -> bool:
    return bool(
        (getattr(b, "basis", "") or "").strip()
        or (getattr(b, "boundary", "") or "").strip()
        or getattr(b, "disconfirmers", None)
        or getattr(b, "affected_by", None)
        or (getattr(b, "trajectory", "") or "").strip()
        or getattr(b, "last_challenged", None)
        or getattr(b, "optionality", None)
    )


def envelope_from_deliberation(delib, *, source: str = "deliberation") -> dict | None:
    """Build an envelope from a Deliberation record. None if nothing specific."""
    if delib is None:
        return None
    extra = getattr(delib, "extra", None) or {}
    contested = bool(getattr(delib, "contested", False))
    antithesis = (getattr(delib, "antithesis", "") or "").strip()
    thesis = (getattr(delib, "thesis", "") or "").strip()
    is_doc = (source or "").startswith("document:")
    disconfirmers = list(extra.get("disconfirmers") or [])
    if is_doc:
        disconfirmers.append(
            "an independent source contradicts this attached document"
        )
    basis = ""
    boundary = ""
    affected: list[str] = []
    last_challenged = None
    if is_doc:
        basis = "Based on a single unverified attached document."
        boundary = "Not independently verified."
        affected = [source, "document"]
    elif contested and thesis:
        basis = _clip(f"Based on the deliberated claim: {thesis}", _MAX_BASIS)
        affected = ["deliberation"]
        if antithesis and "no substantive objection" not in antithesis.lower():
            last_challenged = _now()
    # Uncontested / inferred: do not invent a generic basis (that is boilerplate).
    env = {
        "basis": basis,
        "boundary": boundary,
        "disconfirmers": disconfirmers,
        "affected_by": affected,
        "last_challenged": last_challenged,
    }
    return sanitize_envelope(env)


def optionality_for_enable(flag: str) -> dict:
    return {
        "lost": f"keeping {flag} off",
        "whose": "the user",
        "technical_reversibility": True,
        "practical_reversibility": True,
        "dependency_lock_in": False,
        "rollback": f":disable {flag}",
    }


def optionality_for_allow(path: str = "") -> dict:
    where = f" {path}" if path else " this folder"
    return {
        "lost": f"keeping{where} out of searchable scope",
        "whose": "the user",
        "technical_reversibility": True,
        "practical_reversibility": True,
        "dependency_lock_in": False,
        "rollback": ":allow drop N",
    }


def format_optionality(opt: dict | None) -> str:
    if not opt:
        return ""
    whose = (opt.get("whose") or "unspecified").strip()
    lost = (opt.get("lost") or "unspecified options").strip()
    tech = "yes" if opt.get("technical_reversibility") else "no"
    prac = "yes" if opt.get("practical_reversibility") else "no"
    lock = "yes" if opt.get("dependency_lock_in") else "no"
    rb = (opt.get("rollback") or "").strip()
    line = (f"Optionality for {whose}: losing {lost}. "
            f"Technical reverse {tech}; practical reverse {prac}; "
            f"lock-in {lock}.")
    if rb:
        line += f" Rollback: {rb}."
    return line


def format_belief_inspection(b) -> str:
    """Compact :why / :belief view. Conclusions only — no chain-of-thought."""
    text = (getattr(b, "text", "") or "").strip() or "(empty belief)"
    basis = (getattr(b, "basis", "") or "").strip() or "(not recorded)"
    boundary = (getattr(b, "boundary", "") or "").strip() or "(not recorded)"
    discs = [d for d in (getattr(b, "disconfirmers", None) or []) if str(d).strip()]
    dissent = (getattr(b, "dissent", "") or "").strip()
    contested = bool(getattr(b, "contested", False))
    if contested and dissent:
        surviving = dissent
    elif dissent:
        surviving = dissent
    else:
        surviving = "(none — flagged low-information)" if not contested else "(none recorded)"
    if discs:
        reopen = "\n".join(f"• {d}" for d in discs)
    else:
        reopen = "(not recorded)"
    affected = [a for a in (getattr(b, "affected_by", None) or []) if str(a).strip()]
    rid = getattr(b, "id", "") or ""
    lines = [
        "Current belief",
        "──────────────",
        text,
        "",
        "Why",
        basis,
        "",
        "Still uncertain",
        boundary,
        "",
        "Would reopen if",
        reopen,
        "",
        "Surviving objection",
        surviving,
    ]
    if affected:
        lines.extend(["", "Affected by", "\n".join(f"• {a}" for a in affected)])
    traj = (getattr(b, "trajectory", "") or "").strip()
    if traj:
        lines.extend(["", "Trajectory", traj])
    opt = getattr(b, "optionality", None)
    opt_line = format_optionality(opt) if opt else ""
    if opt_line:
        lines.extend(["", opt_line])
    lc = getattr(b, "last_challenged", None)
    if lc is not None:
        try:
            shown = lc.isoformat() if hasattr(lc, "isoformat") else str(lc)
        except Exception:
            shown = str(lc)
        lines.extend(["", f"Last challenged  {shown}"])
    if rid:
        lines.extend(["", f"id  {rid}"])
    return "\n".join(lines)


def format_no_belief(query: str = "") -> str:
    q = (query or "").strip()
    if q:
        return f"No durable belief matches {q!r}."
    return "No durable beliefs yet."


# ---------------------------------------------------------------------------
# Append-only ledgers (fail-safe; never raise to callers)
# ---------------------------------------------------------------------------

def _append_jsonl(name: str, record: dict) -> None:
    try:
        with _LEDGER_LOCK:
            _LEDGER_DIR.mkdir(exist_ok=True)
            path = _LEDGER_DIR / name
            with open(path, "a") as f:
                f.write(json.dumps(record, default=str) + "\n")
    except Exception as e:
        logger.error(f"failed to append {name}: {e}")


def _read_jsonl(name: str) -> list[dict]:
    path = _LEDGER_DIR / name
    if not path.exists():
        return []
    rows = []
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    except Exception as e:
        logger.error(f"failed to read {name}: {e}")
    return rows


def record_correction_event(*, target: str, target_id: str = "",
                            signal_received: bool, state_changed: bool,
                            reason_if_not: str = "", note: str = "") -> dict:
    """Audit: when a correction signal arrived, did persistent state move?

    Not a score. Empty reason_if_not when state_changed is True.
    """
    rec = {
        "timestamp": _now().isoformat(),
        "target": target,
        "target_id": target_id or "",
        "signal_received": bool(signal_received),
        "state_changed": bool(state_changed),
        "reason_if_not": (reason_if_not or "") if not state_changed else "",
        "note": note or "",
    }
    _append_jsonl("corrections.jsonl", rec)
    return rec


def load_correction_events() -> list[dict]:
    return _read_jsonl("corrections.jsonl")


def record_trajectory_step(*, direction: str, summary: str,
                           optionality: dict | None = None) -> dict:
    rec = {
        "timestamp": _now().isoformat(),
        "direction": direction,
        "summary": _clip(summary, 200),
        "optionality": optionality if isinstance(optionality, dict) else None,
    }
    _append_jsonl("trajectory.jsonl", rec)
    return rec


def load_trajectory_steps() -> list[dict]:
    return _read_jsonl("trajectory.jsonl")


def steps_in_direction(direction: str) -> list[dict]:
    return [s for s in load_trajectory_steps()
            if (s.get("direction") or "") == direction]


def format_trajectory_review(direction: str, new_summary: str,
                             prior: list[dict],
                             optionality: dict | None = None) -> str:
    earlier = "; ".join(
        (s.get("summary") or "").strip() or "(unnamed)"
        for s in prior[-5:]
    ) or "(none)"
    together = earlier + "; " + (new_summary or "this step")
    lines = [
        "[trajectory] This is another small step in a recorded direction.",
        f"Earlier: {earlier}",
        f"Together: {together}",
        "Would this still make sense if all of these steps were viewed as one trajectory?",
        "Not blocked.",
    ]
    opt_line = format_optionality(optionality)
    if opt_line:
        lines.append(opt_line)
    return "\n".join(lines)


def note_gated_step(direction: str, summary: str,
                    optionality: dict | None = None) -> str | None:
    """Record a gated step. Return review text iff this is another step
    in an already-recorded direction. Never blocks. None = no extra UI.

    Ordinary chat must never call this.
    """
    if direction not in GATED_DIRECTIONS:
        return None
    prior = steps_in_direction(direction)
    record_trajectory_step(direction=direction, summary=summary,
                           optionality=optionality)
    if not prior:
        return None
    return format_trajectory_review(direction, summary, prior, optionality)


def format_trajectory_listing(limit: int = 8) -> str:
    steps = load_trajectory_steps()
    if not steps:
        return "No cumulative trajectory recorded yet."
    show = steps[-limit:]
    lines = ["Recorded trajectory (oldest of this slice first):"]
    for s in show:
        ts = (s.get("timestamp") or "")[:19]
        d = s.get("direction") or "?"
        sm = s.get("summary") or ""
        lines.append(f"  {ts}  {d}: {sm}")
    lines.append(
        "Would this still make sense if all of these steps were viewed as one trajectory?"
    )
    return "\n".join(lines)
