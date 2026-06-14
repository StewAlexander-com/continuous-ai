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

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Context formatting for injection
# ---------------------------------------------------------------------------

_RESTORE_PROMPT_PATH = Path(__file__).parent / "prompts" / "context_restore.txt"


def _format_context_injection(state: ContextState) -> str:
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

Durable facts about you and the user (persona memory):
[PERSONA]

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

    def __init__(self, adapter_version: int = 0, base_model: str = "llama3.2"):
        self.adapter_version = adapter_version
        self.base_model = base_model
        self._state: ContextState | None = None
        storage.init_db()
        self._register_signal_handlers()

    def _register_signal_handlers(self):
        """Register SIGINT/SIGTERM to trigger graceful_pause instead of SIGKILL."""
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

    def _handle_signal(self, signum, frame):
        logger.info(f"Signal {signum} received — initiating graceful_pause()")
        self.graceful_pause()
        sys.exit(0)

    # -----------------------------------------------------------------------
    # Core API
    # -----------------------------------------------------------------------

    def restore_context(self, fresh: bool = False) -> str:
        """
        Load the latest MCM state and return a formatted injection string.

        If fresh=True, zero-initializes a new ContextState (no prior context loaded).
        Returns the injection string to be prepended to the system prompt.
        """
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
        injection = _format_context_injection(loaded)
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
        storage.save_context_state(self._state)
        logger.info(
            f"Delta written: thread={delta.thread_id} "
            f"coherence={delta.coherence_score:.2f} "
            f"emergent={delta.emergent}"
        )

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
                f"  last emergent  : {latest.emergent}",
                f"  last insight   : {latest.insight_gained[:80]}",
            ]
        if s.persona.facts:
            lines.append(f"  persona facts  : {len(s.persona.facts)}")
            for f in sorted(s.persona.facts, key=lambda x: x.reinforce_count, reverse=True)[:5]:
                lines.append(f"    • [{f.kind} x{f.reinforce_count}] {f.text[:64]}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# __main__
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    mcm = MCM()
    injection = mcm.restore_context()
    print("\n--- Context Injection String ---")
    print(injection)
    print("\n--- MCM Summary ---")
    print(mcm.summary())
