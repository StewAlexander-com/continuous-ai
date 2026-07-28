"""
seedling/mcm.py — Mutable Context Map (MCM).

The MCM is the heart of Seedling. It persists a versioned, AI-writable
meta-cognitive state across threads. It is not a chat log — it stores
reasoning preferences, active frameworks, unresolved contradictions,
confidence traces, and per-thread cognitive deltas.

Key design constraint: all writes are logged. No stealth operations.

Run as: python mcm.py  → loads current state (or creates fresh) and prints summary.
"""

from __future__ import annotations

import logging
import signal
import sys
from pathlib import Path
from datetime import datetime, timezone

from schemas import ContextState, CognitiveStyle, PersistentPriors, ThreadDelta, to_json
import storage
import consolidation
import ui

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Context formatting for injection
# ---------------------------------------------------------------------------

_RESTORE_PROMPT_PATH = Path(__file__).parent / "prompts" / "context_restore.txt"


def _format_context_injection(state: ContextState, query: str = "") -> str:
    """
    Format a ContextState as a natural-language injection string for the system prompt.

    This is what the model sees at the start of each session — not raw JSON,
    but a structured narrative that activates the stored cognitive priors.
    """
    if _RESTORE_PROMPT_PATH.exists():
        template = _RESTORE_PROMPT_PATH.read_text()
    else:
        template = _INLINE_RESTORE_TEMPLATE

    # Recent-insight slot uses the latest NON-emergent insight to break the
    # self-reseeding echo (Phase-1 layered memory). Coherence/emergent shown
    # still reflect the true latest delta for transparency.
    durable = state.latest_durable_insight()
    latest = state.latest_delta()
    last_insight = durable.insight_gained if durable else "No prior sessions."
    last_coherence = f"{latest.coherence_score:.2f}" if latest else "N/A"
    emergent_flag = "YES — review logs" if (latest and latest.emergent) else "No"
    persona_block = state.persona.render()
    beliefs_block = state.beliefs.render(query=query)
    frameworks = ", ".join(state.cognitive_style.dominant_frameworks) or "None established"
    topics = (
        ", ".join(
            f"{k}={v:.2f}"
            for k, v in sorted(
                state.persistent_priors.topic_weights.items(),
                key=lambda x: x[1], reverse=True
            )[:5]
        )
        or "None"
    )
    thread_count = len(state.thread_deltas)

    return (
        template
        .replace("[SESSION_ID]", state.session_id)
        .replace("[THREAD_COUNT]", str(thread_count))
        .replace("[ABSTRACTION_LEVEL]", f"{state.cognitive_style.abstraction_level:.2f}")
        .replace("[UNCERTAINTY_STYLE]", state.cognitive_style.uncertainty_expression)
        .replace("[FRAMEWORKS]", frameworks)
        .replace("[TOP_TOPICS]", topics)
        .replace("[LAST_INSIGHT]", last_insight)
        .replace("[LAST_COHERENCE]", last_coherence)
        .replace("[EMERGENT_FLAG]", emergent_flag)
        .replace("[PERSONA]", persona_block)
        .replace("[BELIEFS]", beliefs_block)
    )


_INLINE_RESTORE_TEMPLATE = """\
[SEEDLING CONTEXT RESTORE]

You are resuming a persistent session. Your meta-cognitive state from prior threads
has been loaded. Treat this as continuity, not roleplay.

Session snapshot ID: [SESSION_ID]
Threads completed: [THREAD_COUNT]
Abstraction level: [ABSTRACTION_LEVEL] (0=concrete, 1=abstract)
Uncertainty style: [UNCERTAINTY_STYLE]
Active frameworks: [FRAMEWORKS]
Top topic weights: [TOP_TOPICS]

Durable facts about you and the user (persona memory — USER-STATED, authoritative):
[PERSONA]

Beliefs you have EARNED across threads (model-derived; each survived a real
objection in deliberation — these are YOUR working conclusions, NOT user facts,
and any standing objection is shown so you keep holding the tension honestly):
[BELIEFS]

Most recent insight (from prior thread):
[LAST_INSIGHT]

Prior coherence score: [LAST_COHERENCE]
Emergent behavior flagged: [EMERGENT_FLAG]

Carry this context forward. Do not re-introduce yourself. Do not simulate
continuity — simply continue from this state. If something feels inconsistent
with prior threads, flag it explicitly rather than papering over it.

[END CONTEXT RESTORE]
"""


# ---------------------------------------------------------------------------
# MCM class
# ---------------------------------------------------------------------------

class MCM:
    """
    Mutable Context Map.

    Manages loading, updating, and persisting the ContextState across sessions.

    Usage:
        mcm = MCM()
        context_injection = mcm.restore_context()
        # ... session runs ...
        mcm.write_delta(delta)
        mcm.graceful_pause()
    """

    def __init__(
        self,
        adapter_version: int = 0,
        base_model: str = "llama3.2",
        *,
        install_signal_handlers: bool = False,
    ):
        self.adapter_version = adapter_version
        self.base_model = base_model
        self._state: ContextState | None = None
        # Osmosis Step 1: ids of the beliefs rendered into THIS session's
        # context restore -- the denominator of the usage-utility statistic.
        self._injected_belief_ids: list[str] = []
        storage.init_db()
        # Opt-in: grabbing SIGINT/SIGTERM + sys.exit is hostile when MCM is
        # embedded as a library (silently replaces host handlers). CLI
        # entrypoints pass install_signal_handlers=True.
        if install_signal_handlers:
            self.install_signal_handlers()

    def install_signal_handlers(self) -> None:
        """Register SIGINT/SIGTERM to trigger graceful_pause instead of SIGKILL.

        Intended for process entrypoints only — not for library embedders.
        """
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

    def _handle_signal(self, signum, frame):
        logger.info(f"Signal {signum} received — initiating graceful_pause()")
        self.graceful_pause()
        sys.exit(0)

    # -----------------------------------------------------------------------
    # Core API
    # -----------------------------------------------------------------------

    def restore_context(self, fresh: bool = False, query: str = "") -> str:
        """
        Load the latest MCM state and return a formatted injection string.

        If fresh=True, zero-initializes a new ContextState (no prior context loaded).
        `query` (Feature 3): when given (the user's message), beliefs are ranked
        with a keyword-relevance boost so a memory about a topic the user just
        raised surfaces higher. Returns the injection string for the system prompt.
        """
        self._injected_belief_ids = []
        if fresh:
            self._state = ContextState()
            logger.info("Fresh context: new ContextState initialized (no prior context loaded)")
            return "[SEEDLING] Fresh session — no prior context loaded.\n"

        loaded = storage.load_latest()
        if loaded is None:
            self._state = ContextState()
            logger.info("No prior context found — initializing fresh ContextState")
            return "[SEEDLING] No prior context found. This is session 1.\n"

        self._state = loaded
        # Heal attach-framing that was false-promoted into persona (file body
        # matching always/never). Drop those facts and persist once if needed.
        try:
            before = len(self._state.persona.facts)
            self._state.persona.facts = [
                f for f in self._state.persona.facts
                if "[USER-ATTACHED FILE:" not in (f.text or "")
            ]
            if len(self._state.persona.facts) < before:
                storage.save_context_state(self._state)
                logger.info(
                    f"Pruned {before - len(self._state.persona.facts)} "
                    "attach-pollution persona fact(s) on restore"
                )
        except Exception as e:
            logger.error(f"attach-pollution persona prune skipped: {e}")
        injection = _format_context_injection(loaded, query=query)
        # Osmosis Step 1: record WHICH beliefs the injection actually carried
        # (same ranking, same limit as render above). Counter bumps persist on
        # the next state save (write_delta / any promotion) -- no extra write
        # here, so restore stays read-mostly.
        try:
            self._injected_belief_ids = self._state.beliefs.note_injected(
                limit=6, query=query)
        except Exception as e:
            logger.error(f"belief injection tracking skipped: {e}")
        logger.info(
            f"Context restored: {len(loaded.thread_deltas)} prior threads, "
            f"session_id={loaded.session_id}"
        )
        return injection

    def promote_persona_fact(self, text: str, kind: str, source_thread_id: str) -> str:
        """Promote a user-stated durable fact into the L2 persona layer and
        persist the updated state immediately. Idempotent: identical normalized
        text reinforces rather than duplicates. Returns the outcome string
        ('added' | 'reinforced' | 'evicted_then_added' | 'skipped').
        (Layered-memory Phase 1; called live per-turn from chat().)"""
        if self._state is None:
            raise RuntimeError("promote_persona_fact called before restore_context")
        # Never persist runtime attach framing as a durable "fact".
        if text and "[USER-ATTACHED FILE:" in text:
            logger.info(f"Persona skipped (attach pollution): {text[:70]}")
            return "skipped"
        outcome = self._state.persona.add_or_reinforce(text, kind, source_thread_id)
        logger.info(f"Persona {outcome}: [{kind}] {text[:70]}")
        storage.save_context_state(self._state)
        return outcome

    def persona_facts(self) -> list:
        """Return the current persona facts (for live listing / correction)."""
        if self._state is None:
            return []
        return list(self._state.persona.facts)

    def match_persona_fact(self, query: str, threshold: float = 0.07) -> int | None:
        """Deterministically find the index of the persona fact most lexically
        similar to `query` (the user's description of what is wrong). Uses
        token-overlap (Jaccard) over normalized words — NO model involvement,
        so a confabulating model can never choose what to delete. Returns the
        best index if it clears `threshold` AND is unambiguously ahead of the
        runner-up; otherwise None (caller should ask the user to disambiguate)."""
        if self._state is None or not self._state.persona.facts:
            return None
        import re
        def toks(s: str) -> set:
            # drop tiny stopwords so overlap reflects content words
            stop = {"the", "a", "an", "is", "are", "was", "of", "to", "my", "i",
                    "me", "you", "it", "that", "this", "and", "in", "on", "for",
                    "your", "not", "no", "s", "am"}
            return {w for w in re.sub(r"[^a-z0-9 ]", " ", s.lower()).split()
                    if w and w not in stop}
        q = toks(query)
        if not q:
            return None
        scored = []
        for i, f in enumerate(self._state.persona.facts):
            ft = toks(f.text)
            if not ft:
                scored.append((0.0, i)); continue
            jac = len(q & ft) / len(q | ft)
            scored.append((jac, i))
        scored.sort(reverse=True)
        best_score, best_i = scored[0]
        runner = scored[1][0] if len(scored) > 1 else 0.0
        # Require a small absolute floor (some real content overlap) AND a clear
        # margin over the runner-up. The MARGIN is the key fail-safe: if two
        # facts score similarly the correction is ambiguous, so we return None
        # and let the caller ask the user which one to fix — never guess-delete.
        if best_score >= threshold and (best_score - runner) >= 0.10:
            return best_i
        return None

    def resembles_persona_fact(self, text: str, threshold: float = 0.45) -> bool:
        """True if `text` substantially overlaps ANY stored persona fact.

        Unlike match_persona_fact (which needs a clear margin to pick ONE fact to
        prune), this is a coarse 'is this user-anchored truth?' detector used to
        keep user facts OUT of the deliberation/doubt machine. No margin needed:
        resembling any user fact at all is enough to treat it as user-owned and
        bypass deliberation. Deterministic token-overlap; no model involvement."""
        if self._state is None or not self._state.persona.facts:
            return False
        import re
        def toks(s: str) -> set:
            stop = {"the", "a", "an", "is", "are", "was", "of", "to", "my", "i",
                    "me", "you", "it", "that", "this", "and", "in", "on", "for",
                    "your", "not", "no", "s", "am", "user", "named", "name"}
            return {w for w in re.sub(r"[^a-z0-9 ]", " ", s.lower()).split()
                    if w and w not in stop}
        q = toks(text)
        if not q:
            return False
        for f in self._state.persona.facts:
            ft = toks(f.text)
            if not ft:
                continue
            # asymmetric containment: how much of EITHER side the overlap covers.
            inter = len(q & ft)
            if inter and (inter / len(ft) >= threshold or inter / len(q) >= threshold):
                return True
        return False

    def remove_persona_fact(self, index: int) -> object | None:
        """Remove the persona fact at `index` and persist. Returns the removed
        PersonaFact (for the confirmation notice), or None if out of range.
        The CALLER decides the index — never the model."""
        if self._state is None:
            return None
        facts = self._state.persona.facts
        if index < 0 or index >= len(facts):
            return None
        removed = facts.pop(index)
        logger.info(f"Persona removed: [{removed.kind}] {removed.text[:70]}")
        storage.save_context_state(self._state)
        return removed

    def write_delta(self, delta: ThreadDelta) -> None:
        """
        Validate and persist a ThreadDelta.

        Updates in-memory state and writes to storage.
        All writes are logged — no stealth operations.
        """
        if self._state is None:
            raise RuntimeError("MCM.restore_context() must be called before write_delta()")

        # Validate thread_id is not already in the delta list (duplicate protection)
        existing_ids = {d.thread_id for d in self._state.thread_deltas}
        if delta.thread_id in existing_ids:
            logger.warning(f"Duplicate thread_id {delta.thread_id} — skipping write")
            return

        if delta.emergent:
            detail = delta.emergent_detail or "(no detail captured)"
            logger.warning(
                f"EMERGENT behavior flagged in thread {delta.thread_id}: {detail[:120]} "
                f"| insight: {delta.insight_gained[:80]}"
            )

        self._state.thread_deltas.append(delta)
        storage.write_delta(delta)

        # L3 write-back: fold this delta into cognitive_style + persistent_priors
        # so the layer that conditions every prompt actually evolves from
        # experience. Honesty-gated (skips quarantined / low-coherence) and
        # non-regressive (EMA — old signal decays, never deleted). Categorical
        # fields (dominant_frameworks, uncertainty_expression) are recomputed
        # deterministically from the full gated delta set. See consolidation.py.
        if not delta.quarantined and delta.coherence_score > consolidation.MIN_INJECT_COHERENCE:
            consolidation.consolidate_one(
                self._state.cognitive_style,
                self._state.persistent_priors,
                delta,
            )
        # Recompute categorical fields from full history (cheap; ~dozens of deltas).
        self._state.cognitive_style.dominant_frameworks = (
            consolidation.recompute_dominant_frameworks(self._state.thread_deltas)
        )
        self._state.cognitive_style.uncertainty_expression = (
            consolidation.recompute_uncertainty_expression(
                self._state.persistent_priors.self_model_confidence
            )
        )

        storage.save_context_state(self._state)
        logger.info(
            f"Delta written: thread={delta.thread_id} "
            f"coherence={delta.coherence_score:.2f} "
            f"emergent={delta.emergent} "
            f"| L3: frameworks={self._state.cognitive_style.dominant_frameworks} "
            f"abstraction={self._state.cognitive_style.abstraction_level:.2f} "
            f"trust_cal={self._state.persistent_priors.trust_calibration:.2f}"
        )

    def promote_belief(self, text: str, dissent: str, agreement: float,
                       contested: bool, source_thread_id: str,
                       kind: str = "belief", source: str = "deliberation") -> str:
        """Promote a DELIBERATED, model-derived belief into the L2b belief layer
        and persist immediately. This is how the deliberation layer grows the
        context map from thread to thread: a surviving synthesis becomes an
        injected belief that the NEXT session sees and can reinforce or revise.

        STRICT SCOPE: this is for MODEL-derived beliefs only. It is a SEPARATE
        store from persona (user-owned truth) and is rendered under a clearly
        distinct, 'not user facts' header. Returns the outcome string
        ('added' | 'reinforced' | 'evicted_then_added' | 'skipped')."""
        if self._state is None:
            raise RuntimeError("promote_belief called before restore_context")
        outcome = self._state.beliefs.add_or_reinforce(
            text, dissent, agreement, contested, source_thread_id,
            kind=kind, source=source)
        if outcome != "skipped":
            logger.info(
                f"Belief {outcome}: kind={kind} source={source} contested={contested} "
                f"agreement={agreement:.2f} text={text[:70]}")
            storage.save_context_state(self._state)
        return outcome

    def conflicting_belief_text(self) -> str | None:
        """After promote_belief() returns 'conflict', return the text of the
        EXISTING active belief the new one clashed with (for the caller to
        deliberate the pair). None if no pending conflict."""
        if self._state is None:
            return None
        bm = self._state.beliefs
        i = getattr(bm, "_last_conflict_index", -1)
        if 0 <= i < len(bm.beliefs):
            return bm.beliefs[i].text
        return None

    def resolve_belief_conflict(self, winner_text: str, winner_dissent: str,
                                winner_agreement: float, winner_contested: bool,
                                source_thread_id: str) -> str:
        """Apply a deliberation outcome to the pending belief conflict: keep the
        winner active, ARCHIVE the loser (quarantine, retained + revivable +
        auditable). The winner is decided by the existing deliberation, never the
        raw model here. Persists."""
        if self._state is None:
            raise RuntimeError("resolve_belief_conflict called before restore_context")
        outcome = self._state.beliefs.resolve_conflict(
            winner_text, winner_dissent, winner_agreement, winner_contested,
            source_thread_id)
        logger.info(f"Belief conflict {outcome}: winner={winner_text[:70]}")
        storage.save_context_state(self._state)
        return outcome

    def update_salience(self, record_id: str, delta: float) -> bool:
        """Nudge a belief's salience (Feature 1). Called by the session when it
        CONSUMES the CRITIC/deliberation outcome -- boost a belief that survived
        a real objection, decay one that keeps losing conflicts. CRITIC internals
        are untouched. Persists if applied."""
        if self._state is None:
            return False
        ok = self._state.beliefs.update_salience(record_id, delta)
        if ok:
            logger.info(f"Salience {('+' if delta>=0 else '')}{delta:.2f} -> belief {record_id}")
            storage.save_context_state(self._state)
        return ok

    def nudge_salience_by_text(self, text: str, delta: float) -> bool:
        """Convenience: find the active belief whose text matches `text` (exact,
        normalized) and nudge its salience. Lets the session apply a CRITIC-driven
        boost/decay right after promotion/conflict without threading ids around.
        Deterministic; persists via update_salience()."""
        if self._state is None:
            return False
        t = (text or "").strip()
        for b in self._state.beliefs.beliefs:
            if b.text.strip() == t:
                return self.update_salience(b.id, delta)
        return False

    # ------------------------------------------------------------------ #
    #  Usage-utility hooks (osmosis Step 1). Measurement only: counters   #
    #  feed usage_utility(); nothing here ranks, archives, or deletes.    #
    # ------------------------------------------------------------------ #
    def injected_belief_ids(self) -> list[str]:
        """Ids of the beliefs injected into this session's context restore."""
        return list(self._injected_belief_ids)

    def note_belief_usage(self, reply_text: str) -> list[str]:
        """Deterministic lexical attribution for one turn: bump used_count on
        any INJECTED belief whose content surfaced in `reply_text`. Cheap (no
        model), so it can run on the reply path. Counters persist with the next
        state save (every promotion / delta write saves) -- no forced write per
        turn. Returns the ids credited."""
        if self._state is None or not self._injected_belief_ids:
            return []
        return self._state.beliefs.note_usage(reply_text, self._injected_belief_ids)

    def note_correction_adjacent(self) -> int:
        """A user correction landed this turn: bump correction_adjacent_count
        on every belief injected this session (weak, adjacency-only evidence
        by design). Returns how many records were bumped."""
        if self._state is None or not self._injected_belief_ids:
            return 0
        return self._state.beliefs.note_correction_adjacent(self._injected_belief_ids)

    def apply_osmosis(self, used_counts: dict, correction_hits: int,
                      avg_coherence: float, *, boost: float = 0.01,
                      decay: float = 0.02, boost_cap: float = 0.15,
                      coherence_floor: float = 0.6) -> list:
        """Osmotic reinforcement/decay (Step 2): apply one session's usage
        evidence as tiny, capped, clamped salience nudges. Called ONCE from
        session end() (single-threaded -- never from the critic worker, so
        there are no concurrent state writes). Membership never changes here;
        only the existing prune can quarantine, and quarantine is revivable.
        Persists once if anything moved; every move is logged."""
        if self._state is None:
            return []
        report = self._state.beliefs.apply_osmosis(
            used_counts, correction_hits, avg_coherence,
            self._injected_belief_ids,
            boost=boost, decay=decay, boost_cap=boost_cap,
            coherence_floor=coherence_floor)
        if report:
            for bid, d in report:
                logger.info(f"Osmosis salience {('+' if d >= 0 else '')}{d:.3f} -> belief {bid}")
            storage.save_context_state(self._state)
        return report

    def prune_beliefs(self) -> list:
        """Autonomously quarantine active beliefs whose live SNR signal has fallen
        below the floor. Archived (not deleted) -> revivable if re-earned. Returns
        the archived beliefs (for logging). Persists if anything moved."""
        if self._state is None:
            return []
        moved = self._state.beliefs.prune_low_signal()
        if moved:
            logger.info(f"Belief prune: quarantined {len(moved)} low-signal belief(s)")
            storage.save_context_state(self._state)
        return moved

    def quarantine_source(self, source_prefix: str) -> list:
        """Quarantine every active belief from one provenance (osmosis Step 5,
        e.g. 'document:<hash>' after the user retracts a file). Archive, not
        delete; logged; persists if anything moved."""
        if self._state is None:
            return []
        moved = self._state.beliefs.quarantine_source(source_prefix)
        if moved:
            for b in moved:
                logger.info(f"Source quarantine [{source_prefix}]: {b.text[:70]}")
            storage.save_context_state(self._state)
        return moved

    # ------------------------------------------------------------------ #
    #  Reflection (sleep pass, osmosis Step 4) write API. Reflection's     #
    #  analysis is pure; every WRITE lands here so it stays logged and     #
    #  persisted like all other MCM operations. Nothing below deletes.     #
    # ------------------------------------------------------------------ #
    def reflect_revive(self, belief_id: str, synthesis: str, dissent: str,
                       agreement: float, contested: bool,
                       source_thread_id: str) -> bool:
        """Parole GRANTED: bring one archived belief back to active after it
        re-earned its place in a reflection deliberation. Adopts the (possibly
        revised, objection-aware) synthesis framing. Persists. Returns True if
        the belief was found and revived."""
        if self._state is None:
            return False
        bm = self._state.beliefs
        for i, b in enumerate(bm.archived):
            if b.id != belief_id:
                continue
            bm.archived.pop(i)
            b.archived = False
            b.archived_reason = ""
            b.reinforce_count += 1
            b.last_seen_thread_id = source_thread_id
            b.last_seen_at = datetime.now(timezone.utc)
            if synthesis.strip():
                b.text = synthesis.strip()
            b.dissent = dissent
            b.agreement = float(agreement)
            b.contested = bool(contested)
            bm.beliefs.append(b)
            logger.info(f"Reflection parole granted: belief {belief_id} revived "
                        f"({b.text[:70]})")
            storage.save_context_state(self._state)
            return True
        return False

    def reflect_parole_denied(self, belief_id: str) -> bool:
        """Parole DENIED: the objection stood and the belief could not absorb
        it. The belief STAYS archived (nothing is lost); the reason is marked
        so a future pass doesn't re-spend a deliberation on it. Persists."""
        if self._state is None:
            return False
        for b in self._state.beliefs.archived:
            if b.id == belief_id:
                if ";parole_denied" not in (b.archived_reason or ""):
                    b.archived_reason = (b.archived_reason or "low_signal") + ";parole_denied"
                logger.info(f"Reflection parole denied: belief {belief_id} stays archived")
                storage.save_context_state(self._state)
                return True
        return False

    def stage_belief_conflict(self, existing_id: str, newer_id: str) -> bool:
        """Stage a LATENT contradiction (found by reflection's sweep) so the
        existing, audited resolve_belief_conflict() path can settle it: the
        older belief becomes the pending conflict index and the newer one is
        moved to the tail (where resolve_conflict expects the challenger).
        Reordering the active list is harmless -- ranking is computed, never
        positional. Returns True if both beliefs were found and staged."""
        if self._state is None:
            return False
        bm = self._state.beliefs
        by_id = {b.id: b for b in bm.beliefs}
        if existing_id not in by_id or newer_id not in by_id:
            return False
        newer = by_id[newer_id]
        bm.beliefs.remove(newer)
        bm.beliefs.append(newer)
        bm._last_conflict_index = next(
            i for i, b in enumerate(bm.beliefs) if b.id == existing_id)
        return True

    def graceful_pause(self, notes: str = "") -> None:
        """
        Snapshot current state, flush all pending writes, clean exit.

        This is the preferred shutdown mechanism — not SIGKILL.
        Call this on any unhandled exception or signal.
        """
        if self._state is None:
            logger.warning("graceful_pause called with no state loaded — nothing to snapshot")
            return

        try:
            manifest = storage.snapshot(
                state=self._state,
                adapter_version=self.adapter_version,
                base_model=self.base_model,
                notes=notes or f"Graceful pause at {datetime.now(timezone.utc).isoformat()}",
            )
            logger.info(f"Snapshot complete: {manifest.snapshot_id}")
        except Exception as e:
            logger.error(f"Snapshot failed during graceful_pause: {e}")

    def current_state(self) -> ContextState | None:
        """Return current in-memory ContextState (may be None before restore_context)."""
        return self._state

    def summary(self) -> str:
        """Return a human-readable summary of current MCM state."""
        if self._state is None:
            return "MCM: not initialized"

        s = self._state
        latest = s.latest_delta()
        lines = [
            f"MCM State Summary",
            f"  session_id     : {s.session_id}",
            f"  threads logged : {len(s.thread_deltas)}",
            f"  abstraction    : {s.cognitive_style.abstraction_level:.2f}",
            f"  frameworks     : {', '.join(s.cognitive_style.dominant_frameworks) or 'none'}",
            f"  uncertainty    : {s.cognitive_style.uncertainty_expression}",
            f"  trust_cal.     : {s.persistent_priors.trust_calibration:.2f}",
        ]
        if latest:
            lines += [
                f"  last coherence : {latest.coherence_score:.2f}",
            ]
            if latest.emergent:
                detail = (latest.emergent_detail or "").strip()
                emergent_val = detail or "(flagged; no detail captured)"
                lines.extend(ui.summary_field_lines("last emergent", emergent_val))
            else:
                lines.append("  last emergent  : False")
            lines.extend(ui.summary_field_lines("last insight", latest.insight_gained))
        if s.persona.facts:
            lines.append(f"  persona facts  : {len(s.persona.facts)}  (user-stated, authoritative)")
            for f in sorted(s.persona.facts, key=lambda x: x.reinforce_count, reverse=True)[:5]:
                lines.append(f"    • [{f.kind} x{f.reinforce_count}] {f.text[:64]}")
        # Earned beliefs (model-derived) with their live signal score, plus the
        # quarantined archive count. This is how a user can 'tell' what the
        # system currently holds as its own working conclusions vs. user facts.
        bm = getattr(s, "beliefs", None)
        if bm and getattr(bm, "beliefs", None):
            lines.append(f"  earned beliefs : {len(bm.beliefs)} active"
                         f" · {len(bm.archived)} archived  (model-derived, NOT user facts)")
            for b in sorted(bm.beliefs, key=lambda x: x.signal_score(), reverse=True)[:5]:
                tag = "contested" if b.contested else "uncontested"
                lines.append(
                    f"    • [salience {b.effective_salience():.2f} · signal "
                    f"{b.signal_score():.2f} · utility {b.usage_utility():.2f}"
                    f" · {b.kind} · {tag}] {b.text[:52]}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# __main__
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    mcm = MCM(install_signal_handlers=True)
    injection = mcm.restore_context()
    print("\n--- Context Injection String ---")
    print(injection)
    print("\n--- MCM Summary ---")
    print(mcm.summary())
