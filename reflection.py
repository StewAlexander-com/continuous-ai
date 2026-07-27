"""
seedling/reflection.py — the sleep pass (osmosis Step 4).

Aida writes far more experience than she ever re-reads: sub-gate ThreadDeltas
sit inert, quarantined beliefs wait for a live session to accidentally
re-derive them, and latent contradictions between active beliefs go unnoticed
until an insert happens to collide. Reflection is the offline review of that
sediment — run explicitly via `:reflect` (or, opt-in, at session end), NEVER
on the reply path.

Three jobs, deterministic-first:

  1. CONTRADICTION SWEEP — pairwise conflict detection across ACTIVE beliefs
     (the insert-time check only ever sees the incoming belief; paraphrased
     contradictions can coexist until swept). Resolution goes through the
     EXISTING audited conflict machinery: deliberate the pair, keep the
     synthesis, archive the loser (revivable).
  2. ARCHIVE PAROLE — a belief quarantined for low signal may re-earn its
     place, but ONLY with external evidence: its subject must have recurred in
     recent gated deltas (the wallgate pattern — a cheap, model-free pre-gate
     decides WHETHER to spend the deliberation, so reflection cannot ruminate
     itself into reviving everything). Parole is granted only if the
     deliberation produces a synthesis that absorbed the objection.
  3. DELTA MINING — individually sub-gate insights that CONVERGE across >= 3
     distinct threads are jointly coherent: the convergence itself is the
     evidence the honesty gate asked for. One representative candidate is
     deliberated and, if it survives, promoted with source="reflection".

Safety posture (non-regressive by construction):
  - a safety snapshot is written BEFORE any mutation;
  - model spend is hard-capped per pass (reflection_max_deliberations);
  - revivals/promotions consume the session's osmotic promotion budget;
  - every write goes through MCM (logged, persisted); nothing here deletes —
    the only state transitions are revive, promote, resolve (loser archived),
    and parole-denied marking.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from schemas import MIN_INJECT_COHERENCE, ContextState, DeliberatedBelief

logger = logging.getLogger(__name__)

# --- Tunables (deterministic pre-gates) -------------------------------------
PAROLE_RECENT_DELTAS = 10     # how many recent gated deltas count as "recent experience"
PAROLE_OVERLAP = 0.30         # Jaccard(belief, recent insight) to justify a parole hearing
MINE_MIN_THREADS = 3          # convergence across >= this many DISTINCT threads
MINE_JACCARD = 0.50           # lexical equivalence threshold for clustering insights
_PASSTHROUGH = "[deliberation unavailable]"   # deliberation's fail-safe sentinel


def _toks(memory, s: str) -> set:
    """Reuse BeliefMemory's tokenizer so 'same subject' means the same thing
    everywhere (one lexical stance across insert-time and reflection)."""
    return memory._toks(s)


# ---------------------------------------------------------------------------
# Deterministic analysis (pure functions of ContextState; fully testable)
# ---------------------------------------------------------------------------

def recent_gated_insights(state: ContextState, n: int = PAROLE_RECENT_DELTAS) -> list[str]:
    """Insights from the most recent deltas that passed the honesty gate --
    the 'new experience' against which parole evidence is measured."""
    out = []
    for d in reversed(state.thread_deltas):
        if d.quarantined or d.coherence_score <= MIN_INJECT_COHERENCE:
            continue
        if d.insight_gained and d.insight_gained != "No insight extracted.":
            out.append(d.insight_gained)
        if len(out) >= n:
            break
    return out


def parole_candidates(state: ContextState,
                      overlap: float = PAROLE_OVERLAP) -> list[DeliberatedBelief]:
    """Archived-for-low-signal beliefs whose subject RECURRED in recent gated
    experience. Beliefs that lost a conflict are excluded -- they had a fair
    hearing and a winner absorbed them. Already-denied paroles are excluded so
    a pass never re-spends on the same record. Sorted by recurrence strength."""
    bm = state.beliefs
    insights = recent_gated_insights(state)
    if not insights:
        return []
    insight_toks = [_toks(bm, s) for s in insights]
    scored = []
    for b in bm.archived:
        reason = b.archived_reason or ""
        if not reason.startswith("low_signal") or "parole_denied" in reason:
            continue
        bt = _toks(bm, b.text)
        if not bt:
            continue
        best = 0.0
        for it in insight_toks:
            if it:
                best = max(best, len(bt & it) / len(bt | it))
        if best >= overlap:
            scored.append((best, b))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [b for _, b in scored]


def mine_delta_clusters(state: ContextState,
                        min_threads: int = MINE_MIN_THREADS,
                        jaccard: float = MINE_JACCARD) -> list[str]:
    """Candidate insights earned by CONVERGENCE: sub-gate (individually too
    weak to inject), non-quarantined, non-emergent deltas whose insights
    lexically agree across >= min_threads distinct threads. Returns one
    representative text per cluster (the highest-coherence member's framing),
    skipping anything already equivalent to an active belief (a live session
    would just reinforce it -- no deliberation needed)."""
    bm = state.beliefs
    pool = [d for d in state.thread_deltas
            if not d.quarantined and not d.emergent
            and 0.0 < d.coherence_score <= MIN_INJECT_COHERENCE
            and d.insight_gained and d.insight_gained != "No insight extracted."]
    used: set[int] = set()
    candidates = []
    for i, seed in enumerate(pool):
        if i in used:
            continue
        st = _toks(bm, seed.insight_gained)
        if not st:
            continue
        cluster = [seed]
        for j in range(i + 1, len(pool)):
            if j in used:
                continue
            jt = _toks(bm, pool[j].insight_gained)
            if jt and (len(st & jt) / len(st | jt)) >= jaccard:
                cluster.append(pool[j])
                used.add(j)
        threads = {d.thread_id for d in cluster}
        if len(threads) < min_threads:
            continue
        used.add(i)
        rep = max(cluster, key=lambda d: d.coherence_score).insight_gained
        if bm._equivalent_index(rep) is None:
            candidates.append(rep)
    return candidates


def contradiction_pairs(state: ContextState) -> list[tuple[DeliberatedBelief, DeliberatedBelief]]:
    """Latent same-subject, opposite-polarity pairs among ACTIVE beliefs, via
    the SAME detector inserts use (conflict_index) -- reflection widens its
    coverage from 'incoming vs. store' to 'store vs. store'. Deduplicated."""
    bm = state.beliefs
    seen: set[tuple[str, str]] = set()
    pairs = []
    for b in list(bm.beliefs):
        idx = bm.conflict_index(b.text)
        if idx < 0:
            continue
        other = bm.beliefs[idx]
        if other.id == b.id:
            continue
        key = tuple(sorted((b.id, other.id)))
        if key in seen:
            continue
        seen.add(key)
        # older belief = incumbent/"existing"; newer = challenger.
        first, second = ((other, b) if other.formed_at <= b.formed_at else (b, other))
        pairs.append((first, second))
    return pairs


# ---------------------------------------------------------------------------
# Orchestration (spends capped deliberations through existing machinery)
# ---------------------------------------------------------------------------

@dataclass
class ReflectionReport:
    """Printable, inspectable record of one sleep pass -- what was examined,
    what was spent, and what moved. Honest by construction: only mechanism."""
    conflicts_found: int = 0
    conflicts_resolved: int = 0
    paroles_heard: int = 0
    paroles_granted: int = 0
    candidates_mined: int = 0
    candidates_promoted: int = 0
    deliberations_spent: int = 0
    budget_blocked: int = 0
    lines: list[str] = field(default_factory=list)

    def render(self) -> str:
        out = ["=" * 60, "REFLECTION (sleep pass) REPORT", "=" * 60,
               f"  contradictions : {self.conflicts_resolved}/{self.conflicts_found} resolved",
               f"  archive parole : {self.paroles_granted}/{self.paroles_heard} granted",
               f"  delta mining   : {self.candidates_promoted}/{self.candidates_mined} promoted",
               f"  deliberations  : {self.deliberations_spent} spent"]
        if self.budget_blocked:
            out.append(f"  budget-deferred: {self.budget_blocked} (osmotic budget spent)")
        out.extend(f"  - {l}" for l in self.lines)
        out.append("=" * 60)
        return "\n".join(out)


def run_reflection(session, max_deliberations: int = 1,
                   _deliberate=None) -> ReflectionReport:
    """One sleep pass over the session's MCM state. Priority order when
    spending the capped deliberations: contradictions (active-set integrity
    first) > parole (recover benched signal) > mining (admit new convergence).
    `_deliberate` is injectable for tests (house pattern: chat_fn injection).
    Never raises -- reflection must not break a session."""
    rep = ReflectionReport()
    state = session.mcm.current_state()
    if state is None:
        rep.lines.append("no state loaded; nothing to reflect on")
        return rep
    if _deliberate is None:
        from deliberation import deliberate as _deliberate

    # Safety snapshot BEFORE any mutation: every move below is individually
    # non-destructive, but a pre-pass snapshot makes the whole pass reversible.
    try:
        session.mcm.graceful_pause(notes="pre-reflection safety snapshot")
    except Exception as e:
        logger.error(f"reflection: pre-pass snapshot failed, aborting pass: {e}")
        rep.lines.append("aborted: could not write safety snapshot")
        return rep

    def spend_ok() -> bool:
        return rep.deliberations_spent < max(0, int(max_deliberations))

    # --- 1) CONTRADICTION SWEEP (integrity of what is already injected) ---
    try:
        pairs = contradiction_pairs(state)
        rep.conflicts_found = len(pairs)
        for existing, newer in pairs:
            if not spend_ok():
                break
            if not session.mcm.stage_belief_conflict(existing.id, newer.id):
                continue
            rep.deliberations_spent += 1
            session._resolve_belief_conflict(
                newer.text, newer.dissent, newer.agreement, newer.contested)
            rep.conflicts_resolved += 1
            rep.lines.append(f"resolved: \"{newer.text[:60]}\" vs \"{existing.text[:60]}\"")
    except Exception as e:
        logger.error(f"reflection contradiction sweep skipped: {e}")

    # --- 2) ARCHIVE PAROLE (external recurrence earned a hearing) ---
    try:
        for b in parole_candidates(state):
            if not spend_ok():
                break
            if not session._osmosis_budget_available():
                rep.budget_blocked += 1
                break
            rep.deliberations_spent += 1
            rep.paroles_heard += 1
            d = _deliberate(b.text, session.thread_id, session._chat_once,
                            session.model_name)
            if getattr(d, "antithesis", "") == _PASSTHROUGH:
                # machinery failed, not the belief: no verdict, stays eligible
                rep.paroles_heard -= 1
                rep.deliberations_spent -= 1
                continue
            synthesis = (getattr(d, "synthesis", "") or "").strip()
            contested = bool(getattr(d, "contested", False))
            # Granted iff the deliberation ended reconciled: either no real
            # objection survived, or the synthesis ABSORBED it (revised text).
            # A contested verdict with an unchanged thesis means the objection
            # stood -- parole denied, belief stays archived (nothing lost).
            if synthesis and (not contested or synthesis != b.text):
                ok = session.mcm.reflect_revive(
                    b.id, synthesis,
                    getattr(d, "antithesis", "") if contested else "",
                    float(getattr(d, "agreement", 0.5)), contested,
                    session.thread_id)
                if ok:
                    session._osmosis_budget_spend("revived")
                    rep.paroles_granted += 1
                    rep.lines.append(f"parole granted: \"{synthesis[:70]}\"")
            else:
                session.mcm.reflect_parole_denied(b.id)
                rep.lines.append(f"parole denied: \"{b.text[:70]}\"")
    except Exception as e:
        logger.error(f"reflection parole skipped: {e}")

    # --- 3) DELTA MINING (convergence across threads = earned coherence) ---
    try:
        candidates = mine_delta_clusters(state)
        rep.candidates_mined = len(candidates)
        for cand in candidates:
            if not spend_ok():
                break
            if not session._osmosis_budget_available():
                rep.budget_blocked += 1
                break
            rep.deliberations_spent += 1
            d = _deliberate(cand, session.thread_id, session._chat_once,
                            session.model_name)
            if getattr(d, "antithesis", "") == _PASSTHROUGH:
                rep.deliberations_spent -= 1
                continue
            synthesis = (getattr(d, "synthesis", "") or "").strip() or cand
            contested = bool(getattr(d, "contested", False))
            outcome = session.mcm.promote_belief(
                text=synthesis,
                dissent=getattr(d, "antithesis", "") if contested else "",
                agreement=float(getattr(d, "agreement", 0.5)),
                contested=contested, source_thread_id=session.thread_id,
                kind="insight", source="reflection")
            session._osmosis_budget_spend(outcome)
            if outcome == "conflict":
                session._resolve_belief_conflict(
                    synthesis, getattr(d, "antithesis", ""),
                    float(getattr(d, "agreement", 0.5)), contested)
            if outcome not in ("skipped",):
                rep.candidates_promoted += 1
                rep.lines.append(f"mined ({outcome}): \"{synthesis[:70]}\"")
    except Exception as e:
        logger.error(f"reflection mining skipped: {e}")

    logger.info(
        f"Reflection pass: conflicts {rep.conflicts_resolved}/{rep.conflicts_found}, "
        f"parole {rep.paroles_granted}/{rep.paroles_heard}, "
        f"mined {rep.candidates_promoted}/{rep.candidates_mined}, "
        f"spent {rep.deliberations_spent}")
    return rep
