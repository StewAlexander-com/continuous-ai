"""
seedling/schemas.py — All data schemas for the Seedling runtime.

All dataclasses use type hints and docstrings. Pydantic-style validation
is added via __post_init__ where cross-field consistency matters.

Run as: python schemas.py  → prints one of each as JSON.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Literal


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)

def _uuid() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# CognitiveStyle
# ---------------------------------------------------------------------------

@dataclass
class CognitiveStyle:
    """
    Characterizes the reasoning style expressed by the model during a session.

    abstraction_level: 0.0 = highly concrete, 1.0 = highly abstract.
    dominant_frameworks: ordered list of active reasoning frameworks
        (e.g., ["Second Arrow", "Bayesian updating", "systems thinking"]).
    contradiction_tolerance: 0.0 = intolerant (black/white), 1.0 = high.
    uncertainty_expression: how uncertainty is surfaced.
        - "hedged": qualified statements ("probably", "likely")
        - "explicit": direct statements ("I don't know", "confidence: 0.3")
        - "suppressed": uncertainty hidden — WARNING: conflicts with emergent logging.
    """
    abstraction_level: float = 0.5
    dominant_frameworks: list[str] = field(default_factory=list)
    contradiction_tolerance: float = 0.5
    uncertainty_expression: Literal["hedged", "explicit", "suppressed"] = "hedged"

    def __post_init__(self):
        if self.uncertainty_expression == "suppressed":
            import warnings
            warnings.warn(
                "CognitiveStyle.uncertainty_expression='suppressed' conflicts with "
                "emergent=True logging. Emergent signals may be silently dropped.",
                stacklevel=2,
            )
        assert 0.0 <= self.abstraction_level <= 1.0, "abstraction_level must be in [0, 1]"
        assert 0.0 <= self.contradiction_tolerance <= 1.0, "contradiction_tolerance must be in [0, 1]"


# ---------------------------------------------------------------------------
# PersistentPriors
# ---------------------------------------------------------------------------

@dataclass
class PersistentPriors:
    """
    Long-running weighted priors that accumulate across threads.

    topic_weights: keys are topic strings, values are salience weights (0.0–1.0).
        Keys are unioned across sessions; missing keys default to 0.0 for distance calcs.
    trust_calibration: how much the model trusts user corrections (0=ignore, 1=fully defer).
    self_model_confidence: confidence in the model's own self-assessment accuracy.
    """
    topic_weights: dict[str, float] = field(default_factory=dict)
    trust_calibration: float = 0.5
    self_model_confidence: float = 0.5

    def __post_init__(self):
        assert 0.0 <= self.trust_calibration <= 1.0
        assert 0.0 <= self.self_model_confidence <= 1.0
        for k, v in self.topic_weights.items():
            assert 0.0 <= v <= 1.0, f"topic_weight '{k}' must be in [0, 1]"

    def topic_vector(self, universe: set[str]) -> list[float]:
        """Return a dense vector over a shared topic universe (0-fills missing keys)."""
        return [self.topic_weights.get(t, 0.0) for t in sorted(universe)]


# ---------------------------------------------------------------------------
# ThreadDelta
# ---------------------------------------------------------------------------

@dataclass
class ThreadDelta:
    """
    The cognitive differential produced at the end of each thread.

    Captures what changed — not what was said. This is the write-back unit
    that gives Seedling causal continuity between sessions.

    thread_id: must match the session_id of the session that produced this delta.
    weight_adjustment_signal: composite signal fed to RDST scorer (-1.0 to 1.0).
    emergent: True if any unexpected model behavior was observed during this thread.
    """
    thread_id: str = field(default_factory=_uuid)
    timestamp: datetime = field(default_factory=_now)
    insight_gained: str = ""
    coherence_score: float = 0.0        # 0.0–1.0, from Critic
    user_correction_count: int = 0
    weight_adjustment_signal: float = 0.0   # -1.0 to 1.0
    emergent: bool = False
    emergent_detail: str = ""   # the actual emergent behavior text, when emergent=True
    frameworks_used: list[str] = field(default_factory=list)

    def __post_init__(self):
        assert 0.0 <= self.coherence_score <= 1.0, "coherence_score must be in [0, 1]"
        assert -1.0 <= self.weight_adjustment_signal <= 1.0, "weight_adjustment_signal must be in [-1, 1]"


# ---------------------------------------------------------------------------
# PersonaMemory (L2) — layered memory, Phase 1
#
# Independent implementation inspired by ideas from Mem0 (Apache-2.0) and
# TencentDB-Agent-Memory. No code from either project is used — only the
# high-level concepts of memory layering and promote-don't-overwrite recall.
# See docs/design/memory-layering.md.
# ---------------------------------------------------------------------------

@dataclass
class PersonaFact:
    """A durable, promoted memory — an identity or stable preference that traced
    to an explicit user statement. Provenance (source_thread_id) enables
    drill-down to the L0 transcript."""
    text: str = ""
    kind: str = "identity"            # "identity" | "preference" | "constraint"
    source_thread_id: str = ""
    promoted_at: datetime = field(default_factory=_now)
    reinforce_count: int = 1


@dataclass
class PersonaMemory:
    """L2 layer: a small, capped, always-injected set of durable facts.

    Phase 1 promotes ONLY user-stated facts (gated on a real user utterance),
    with exact-normalized dedup (no fuzzy/embedding merge — that is deferred to
    Phase 2 to avoid corrupting distinct identity facts).
    """
    facts: list[PersonaFact] = field(default_factory=list)
    cap: int = 12

    @staticmethod
    def _norm(s: str) -> str:
        import re
        return re.sub(r"[^a-z0-9 ]", "", s.lower()).strip()

    def add_or_reinforce(self, text: str, kind: str, source_thread_id: str) -> str:
        """Add a new fact, or reinforce an existing one with identical normalized
        text. Returns 'added' | 'reinforced' | 'evicted_then_added'. Caps size
        by evicting the lowest reinforce_count (then oldest) fact."""
        key = self._norm(text)
        if not key:
            return "skipped"
        for f in self.facts:
            if self._norm(f.text) == key:
                f.reinforce_count += 1
                return "reinforced"
        self.facts.append(PersonaFact(
            text=text, kind=kind, source_thread_id=source_thread_id,
        ))
        if len(self.facts) > self.cap:
            # evict lowest reinforce_count, then oldest
            self.facts.sort(key=lambda f: (f.reinforce_count, f.promoted_at))
            self.facts.pop(0)
            return "evicted_then_added"
        return "added"

    def render(self, limit: int = 12) -> str:
        """Human-readable block for the context-restore injection."""
        if not self.facts:
            return "None established."
        top = sorted(self.facts, key=lambda f: f.reinforce_count, reverse=True)[:limit]
        return "\n".join(f"  - {f.text}" for f in top)


# ---------------------------------------------------------------------------
# DeliberatedBeliefs (L2b: MODEL-derived, earned through friction)
# ---------------------------------------------------------------------------
# Cross-thread home for the deliberation layer's output. Kept STRICTLY SEPARATE
# from PersonaMemory: persona = user-owned truth (verbatim); beliefs = the
# model's OWN insights that survived a real objection. The wall between them is
# the whole point. Beliefs grow over time: a later thread that re-derives an
# equivalent belief reinforces it; consensus-only insights are admitted weakly
# (low information) and decay out first.

@dataclass
class DeliberatedBelief:
    """One belief that came out of a deliberation and is carried across threads.
    Provenance (agreement, contested, source thread) makes it auditable; it
    traces back to a ledger record and ultimately an L0 transcript."""
    text: str = ""                     # the synthesis (revised, objection-aware)
    dissent: str = ""                  # the surviving objection (preserved)
    agreement: float = 0.5             # 0..1; low = strongly contested (high info)
    contested: bool = True             # did a substantive objection survive?
    source_thread_id: str = ""
    formed_at: datetime = field(default_factory=_now)
    reinforce_count: int = 1           # re-derived in a later thread => bumped
    last_seen_thread_id: str = ""
    # --- autonomous SNR calculus + conflict/quarantine (all additive) ---
    last_seen_at: datetime = field(default_factory=_now)   # for recency decay
    challenged_count: int = 0          # times it lost/was challenged by a conflict
    archived: bool = False             # quarantined: not injected, but retained
    archived_reason: str = ""          # "low_signal" | "lost_conflict:<text>" | ""

    def signal_score(self, now: "datetime | None" = None) -> float:
        """A deterministic 0..~ score: how much SIGNAL this belief carries right
        now. Grows with how often it was independently re-earned (reinforce_count)
        and how much information it holds (a contested belief that survived a real
        objection is higher-signal than a bland consensus); shrinks with how long
        it has gone untouched (recency decay) and how often it lost a conflict.
        Pure arithmetic on stored fields -- no model, fully auditable."""
        from datetime import datetime as _dt, timezone as _tz
        now = now or _dt.now(_tz.utc)
        # information content: contested+low-agreement beliefs survived friction
        info = (1.0 - float(self.agreement)) if self.contested else 0.15
        # earned repetition: diminishing returns so one loud belief can't dominate
        import math as _m
        earned = _m.log1p(max(0, self.reinforce_count))
        # recency: half-life ~45 days since last seen (gentle, never zero)
        try:
            seen = self.last_seen_at
            if isinstance(seen, str):
                seen = _dt.fromisoformat(seen)
            age_days = max(0.0, (now - seen).total_seconds() / 86400.0)
        except Exception:
            age_days = 0.0
        recency = 0.5 ** (age_days / 45.0)
        # conflict penalty: repeatedly losing challenges erodes signal
        penalty = 1.0 / (1.0 + 0.5 * max(0, self.challenged_count))
        return (0.35 + 0.65 * info) * (1.0 + earned) * recency * penalty


@dataclass
class BeliefMemory:
    """L2b layer: a small, capped, always-injected set of EARNED beliefs.

    Mirrors PersonaMemory's shape (capped, reinforce-counted, deterministic
    dedup) but is model-owned. Equivalence is decided by token-overlap (Jaccard),
    NOT embeddings -- same conservative choice persona made, so distinct beliefs
    are never silently merged. Eviction prefers the WEAKEST belief so what
    survives is what kept earning its place across threads.
    """
    beliefs: list[DeliberatedBelief] = field(default_factory=list)
    archived: list[DeliberatedBelief] = field(default_factory=list)  # quarantine
    cap: int = 8
    merge_threshold: float = 0.55      # Jaccard >= this => same belief (reinforce)
    prune_floor: float = 0.12          # signal below this => quarantine (not delete)
    conflict_threshold: float = 0.4    # token overlap to consider two beliefs "about" the same thing

    @staticmethod
    def _toks(s: str) -> set:
        import re as _re
        stop = {"the", "a", "an", "is", "are", "was", "of", "to", "it", "that",
                "this", "and", "in", "on", "for", "not", "no", "with", "as",
                "by", "or", "be", "can", "only", "when", "under", "its"}
        return {w for w in _re.sub(r"[^a-z0-9 ]", " ", s.lower()).split()
                if w and w not in stop}

    def _equivalent_index(self, text: str):
        """Index of an existing belief lexically equivalent to `text`, or None.
        Deterministic; no model involvement."""
        q = self._toks(text)
        if not q:
            return None
        best_i, best_j = None, 0.0
        for i, b in enumerate(self.beliefs):
            bt = self._toks(b.text)
            if not bt:
                continue
            jac = len(q & bt) / len(q | bt)
            if jac > best_j:
                best_i, best_j = i, jac
        return best_i if best_j >= self.merge_threshold else None

    def add_or_reinforce(self, text: str, dissent: str, agreement: float,
                         contested: bool, source_thread_id: str) -> str:
        """Add an earned belief, or reinforce an equivalent existing one. A
        re-derived belief bumps reinforce_count and adopts the MORE contested
        (higher-information, lower-agreement) framing. Returns
        'added' | 'reinforced' | 'evicted_then_added' | 'skipped'."""
        from datetime import datetime as _dt, timezone as _tz
        text = (text or "").strip()
        if not text:
            return "skipped"
        now = _dt.now(_tz.utc)
        # A re-derived belief may already be sitting in quarantine -> REVIVE it
        # (nothing is ever truly lost; signal can come back).
        if self.revive_if_present(text, source_thread_id):
            return "revived"
        # CONFLICT FIRST: if this belief contradicts an active one (same subject,
        # opposite polarity), do NOT silently merge it as agreement -- the token
        # dedup can't see negation. Surface a 'conflict' so the caller resolves
        # the pair via the existing deliberation, then calls resolve_conflict().
        # The belief is still added below if the caller doesn't handle it, so a
        # contradiction can never cause silent loss.
        cidx = self.conflict_index(text)
        if cidx >= 0:
            self._last_conflict_index = cidx
            self.beliefs.append(DeliberatedBelief(
                text=text, dissent=dissent, agreement=agreement, contested=contested,
                source_thread_id=source_thread_id, last_seen_thread_id=source_thread_id,
                last_seen_at=now,
            ))
            return "conflict"
        idx = self._equivalent_index(text)
        if idx is not None:
            b = self.beliefs[idx]
            b.reinforce_count += 1
            b.last_seen_thread_id = source_thread_id
            b.last_seen_at = now
            if agreement < b.agreement:   # keep the more informative framing
                b.text, b.dissent = text, dissent
                b.agreement, b.contested = agreement, contested
            return "reinforced"
        self.beliefs.append(DeliberatedBelief(
            text=text, dissent=dissent, agreement=agreement, contested=contested,
            source_thread_id=source_thread_id, last_seen_thread_id=source_thread_id,
            last_seen_at=now,
        ))
        if len(self.beliefs) > self.cap:
            # Over cap: ARCHIVE the lowest-signal belief (quarantine, not delete)
            # using the live SNR calculus -- so what stays injected is what still
            # carries signal, and the loser is retained + revivable + auditable.
            self.beliefs.sort(key=lambda b: b.signal_score(now))
            self._archive(self.beliefs.pop(0), "low_signal")
            return "evicted_then_added"
        return "added"

    # ------------------------------------------------------------------ #
    #  Autonomous SNR pruning + conflict handling (all deterministic).   #
    #  Quarantine (archive) instead of delete => non-regressive: a belief #
    #  the model would still hold is never silently destroyed.           #
    # ------------------------------------------------------------------ #
    def _archive(self, b: DeliberatedBelief, reason: str) -> None:
        b.archived = True
        b.archived_reason = reason
        self.archived.append(b)

    def revive_if_present(self, text: str, source_thread_id: str) -> bool:
        """If `text` matches an ARCHIVED belief, bring it back to active with a
        reinforcement bump. Returns True if a revival happened. This is what makes
        pruning safe: re-earning a quarantined belief restores it."""
        from datetime import datetime as _dt, timezone as _tz
        q = self._toks(text)
        if not q or not self.archived:
            return False
        for i, b in enumerate(self.archived):
            bt = self._toks(b.text)
            if bt and (len(q & bt) / len(q | bt)) >= self.merge_threshold:
                b.archived = False
                b.archived_reason = ""
                b.reinforce_count += 1
                b.last_seen_thread_id = source_thread_id
                b.last_seen_at = _dt.now(_tz.utc)
                self.archived.pop(i)
                self.beliefs.append(b)
                return True
        return False

    def conflict_index(self, text: str) -> int:
        """Index of an ACTIVE belief that is about the same subject as `text` but
        DISAGREES with it (one negates while the other does not, over shared
        content words). Deterministic lexical heuristic -- no embeddings, no model.
        Returns -1 if no conflict. The conflict is RESOLVED elsewhere by running
        the pair through the existing deliberation; this only DETECTS it."""
        import re as _re
        def negated(s: str) -> bool:
            return bool(_re.search(r"\b(not|never|no|cannot|can't|isn't|aren't|"
                                   r"doesn't|don't|fails?|false|without|rarely)\b",
                                   s.lower()))
        q = self._toks(text); qn = negated(text)
        if not q:
            return -1
        best_i, best_overlap = -1, 0.0
        for i, b in enumerate(self.beliefs):
            bt = self._toks(b.text)
            if not bt:
                continue
            overlap = len(q & bt) / len(q | bt)
            # same subject (decent shared content) but OPPOSITE polarity
            if overlap >= self.conflict_threshold and (qn != negated(b.text)):
                if overlap > best_overlap:
                    best_i, best_overlap = i, overlap
        return best_i

    def resolve_conflict(self, winner_text: str, winner_dissent: str,
                         winner_agreement: float, winner_contested: bool,
                         source_thread_id: str) -> str:
        """Apply the OUTCOME of a deliberation between the just-added conflicting
        belief and the existing one it clashed with (recorded in
        _last_conflict_index). The caller decides `winner_text` via the existing
        deliberation machinery -- never the raw model here. The LOSER is archived
        with provenance ('lost_conflict:<winner>'); the winner stays active and
        absorbs the conflict (challenged_count bumped on the loser). Call this
        right after add_or_reinforce() returns 'conflict'."""
        from datetime import datetime as _dt, timezone as _tz
        now = _dt.now(_tz.utc)
        existing_i = getattr(self, "_last_conflict_index", -1)
        self._last_conflict_index = -1
        # The new (just-appended) belief is the last active one.
        new_b = self.beliefs[-1] if self.beliefs else None
        existing_b = self.beliefs[existing_i] if 0 <= existing_i < len(self.beliefs) else None
        wtok = self._toks(winner_text)
        # Decide which of the two the winner text matches; archive the other.
        def matches(b):
            if b is None:
                return 0.0
            bt = self._toks(b.text)
            return (len(wtok & bt) / len(wtok | bt)) if bt and wtok else 0.0
        keep, drop = (new_b, existing_b)
        if matches(existing_b) > matches(new_b):
            keep, drop = existing_b, new_b
        if drop is not None and drop in self.beliefs and drop is not keep:
            drop.challenged_count += 1
            self.beliefs.remove(drop)
            self._archive(drop, "lost_conflict:%s" % winner_text[:80])
        # Refresh the surviving belief to the (possibly revised) winner text.
        if keep is not None:
            keep.text = winner_text
            keep.dissent = winner_dissent
            keep.agreement = winner_agreement
            keep.contested = winner_contested
            keep.last_seen_thread_id = source_thread_id
            keep.last_seen_at = now
            return "conflict_resolved"
        return self.add_or_reinforce(winner_text, winner_dissent, winner_agreement,
                                     winner_contested, source_thread_id)

    def prune_low_signal(self, now=None) -> list:
        """Autonomously quarantine any ACTIVE belief whose live signal has fallen
        below prune_floor. Returns the list of archived beliefs (for logging).
        Safe: archived, not deleted; revives if re-earned later."""
        from datetime import datetime as _dt, timezone as _tz
        now = now or _dt.now(_tz.utc)
        moved = []
        keep = []
        for b in self.beliefs:
            if b.signal_score(now) < self.prune_floor and len(self.beliefs) - len(moved) > 1:
                self._archive(b, "low_signal")
                moved.append(b)
            else:
                keep.append(b)
        self.beliefs = keep
        return moved

    def render(self, limit: int = 6) -> str:
        """Human-readable block for the context-restore injection. Most-earned
        beliefs first. Dissent is shown so the next thread sees the live tension."""
        if not self.beliefs:
            return "None yet -- beliefs form as insights survive objection across threads."
        ranked = sorted(self.beliefs, key=lambda b: b.signal_score(),
                        reverse=True)[:limit]
        lines = []
        for b in ranked:
            tag = ("x%d" % b.reinforce_count) if b.reinforce_count > 1 else "new"
            line = "  - %s  [%s]" % (b.text, tag)
            if b.contested and b.dissent:
                line += "\n      (standing objection: %s)" % b.dissent
            lines.append(line)
        return "\n".join(lines)



# ---------------------------------------------------------------------------
# ContextState
# ---------------------------------------------------------------------------

@dataclass
class ContextState:
    """
    The full meta-cognitive state of the Seedling runtime.

    This is the MCM's primary record. Versioned snapshots of this are
    written to snapshots/ at session end or on graceful_pause().

    session_id: UUID for this snapshot instance (not a thread_id).
    thread_deltas: ordered list of all ThreadDeltas, newest last.
    """
    session_id: str = field(default_factory=_uuid)
    timestamp: datetime = field(default_factory=_now)
    cognitive_style: CognitiveStyle = field(default_factory=CognitiveStyle)
    persistent_priors: PersistentPriors = field(default_factory=PersistentPriors)
    thread_deltas: list[ThreadDelta] = field(default_factory=list)
    persona: "PersonaMemory" = field(default_factory=lambda: PersonaMemory())
    beliefs: "BeliefMemory" = field(default_factory=lambda: BeliefMemory())

    def latest_delta(self) -> ThreadDelta | None:
        """Return the most recent ThreadDelta, or None if no threads yet."""
        return self.thread_deltas[-1] if self.thread_deltas else None

    def latest_durable_insight(self) -> ThreadDelta | None:
        """Return the most recent NON-emergent ThreadDelta (falls back to the
        latest delta if every delta is emergent).

        This feeds the 'most recent insight' slot in the context-restore
        injection. Preferring non-emergent insights breaks the self-reseeding
        loop where an emergent-only tangent (e.g. roleplay) keeps re-injecting
        and re-capturing itself every session.
        """
        for d in reversed(self.thread_deltas):
            if not d.emergent:
                return d
        return self.thread_deltas[-1] if self.thread_deltas else None


# ---------------------------------------------------------------------------
# CriticEvaluation
# ---------------------------------------------------------------------------

@dataclass
class CriticEvaluation:
    """
    Output of the Critic pass on a single model response.

    The Critic is a separate model instance (ideally a different architecture
    or at minimum the base model without adapters) that evaluates each response
    before it is logged to MCM.

    critic_backend: "local" (Ollama base model) or "perplexity" (Perplexity API).
        "perplexity" is strongly preferred — different architecture = genuine gap.
    """
    response_id: str = field(default_factory=_uuid)
    coherence: float = 0.0
    contradiction_detected: bool = False
    drift_risk: float = 0.0
    correction_predicted: bool = False
    notes: str = ""
    critic_backend: Literal["local", "perplexity"] = "local"

    def __post_init__(self):
        assert 0.0 <= self.coherence <= 1.0
        assert 0.0 <= self.drift_risk <= 1.0


# ---------------------------------------------------------------------------
# TuningJob
# ---------------------------------------------------------------------------

@dataclass
class TuningJob:
    """
    A single LoRA adapter tuning run record.

    SAFETY: approved must be True before trigger_tuning() will execute.
    This field is only set by explicit --approve-tuning CLI flag — never
    set programmatically within a session.

    composite_signal: weighted average of Critic scores for training data batch.
    status lifecycle: pending → approved → running → complete | rolled_back
    """
    job_id: str = field(default_factory=_uuid)
    triggered_at: datetime = field(default_factory=_now)
    thread_ids_used: list[str] = field(default_factory=list)
    adapter_version_in: int = 0
    adapter_version_out: int = 0
    approved: bool = False
    composite_signal: float = 0.0
    status: Literal["pending", "approved", "running", "complete", "rolled_back"] = "pending"

    def __post_init__(self):
        assert -1.0 <= self.composite_signal <= 1.0


# ---------------------------------------------------------------------------
# SnapshotManifest
# ---------------------------------------------------------------------------

@dataclass
class SnapshotManifest:
    """
    Metadata record for a point-in-time snapshot of the full MCM state.

    Written to snapshots/ on every graceful_pause() or manual `seedling.py snapshot`.
    Allows full state recovery from any snapshot_id.

    adapter_version: the LoRA adapter version active at snapshot time.
        Version 0 = base model, no adapter.
    """
    snapshot_id: str = field(default_factory=_uuid)
    created_at: datetime = field(default_factory=_now)
    mcm_record_count: int = 0
    adapter_version: int = 0
    base_model: str = "llama3.2"
    notes: str = ""


# ---------------------------------------------------------------------------
# JSON serialization helper
# ---------------------------------------------------------------------------

def _serialize(obj):
    """Custom JSON serializer for datetime and nested dataclasses."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def to_json(dc) -> str:
    """Convert any Seedling dataclass to a JSON string."""
    return json.dumps(asdict(dc), default=_serialize, indent=2)


# ---------------------------------------------------------------------------
# __main__ smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    style = CognitiveStyle(
        abstraction_level=0.7,
        dominant_frameworks=["Second Arrow", "Bayesian updating"],
        contradiction_tolerance=0.6,
        uncertainty_expression="explicit",
    )

    priors = PersistentPriors(
        topic_weights={"physics": 0.8, "AI philosophy": 0.9, "cybersecurity": 0.7},
        trust_calibration=0.6,
        self_model_confidence=0.55,
    )

    delta = ThreadDelta(
        insight_gained="User prefers mechanisms over vibes; avoid pep-talk.",
        coherence_score=0.82,
        user_correction_count=1,
        weight_adjustment_signal=0.15,
        frameworks_used=["Second Arrow", "Gödelian limits"],
    )

    state = ContextState(
        cognitive_style=style,
        persistent_priors=priors,
        thread_deltas=[delta],
    )

    evaluation = CriticEvaluation(
        coherence=0.82,
        contradiction_detected=False,
        drift_risk=0.12,
        correction_predicted=False,
        notes="Response well-grounded. No drift detected.",
        critic_backend="perplexity",
    )

    job = TuningJob(
        thread_ids_used=[delta.thread_id],
        adapter_version_in=0,
        adapter_version_out=1,
        composite_signal=0.78,
        status="pending",
    )

    manifest = SnapshotManifest(
        mcm_record_count=1,
        adapter_version=0,
        base_model="llama3.2",
        notes="Initial snapshot after thread 1.",
    )

    for label, obj in [
        ("ContextState", state),
        ("CriticEvaluation", evaluation),
        ("TuningJob", job),
        ("SnapshotManifest", manifest),
    ]:
        print(f"\n{'='*60}")
        print(f"  {label}")
        print('='*60)
        print(to_json(obj))
