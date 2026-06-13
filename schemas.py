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
    frameworks_used: list[str] = field(default_factory=list)

    def __post_init__(self):
        assert 0.0 <= self.coherence_score <= 1.0, "coherence_score must be in [0, 1]"
        assert -1.0 <= self.weight_adjustment_signal <= 1.0, "weight_adjustment_signal must be in [-1, 1]"


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

    def latest_delta(self) -> ThreadDelta | None:
        """Return the most recent ThreadDelta, or None if no threads yet."""
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
