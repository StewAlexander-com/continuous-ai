"""
seedling/storage.py — LanceDB wrapper for persistent context storage.

Tables:
  context_states   — versioned ContextState records (full snapshots)
  thread_deltas    — append-only ThreadDelta records
  critic_evals     — append-only CriticEvaluation records
  tuning_jobs      — TuningJob records

Run as: python storage.py  → initializes DB and prints table info.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import lancedb
import pyarrow as pa

from schemas import (
    ContextState, ThreadDelta, CriticEvaluation,
    SnapshotManifest, to_json, _serialize,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# DB path (relative to project root, resolved at runtime)
# ---------------------------------------------------------------------------

_DB_PATH = Path(__file__).parent / ".seedling_db"


def _serialize_record(dc) -> dict:
    """Flatten dataclass to JSON-safe dict with datetime → ISO string."""
    raw = asdict(dc)
    return json.loads(json.dumps(raw, default=_serialize))


# ---------------------------------------------------------------------------
# Schema definitions (PyArrow — LanceDB requires explicit schemas)
# ---------------------------------------------------------------------------

_THREAD_DELTA_SCHEMA = pa.schema([
    pa.field("thread_id", pa.string()),
    pa.field("timestamp", pa.string()),
    pa.field("insight_gained", pa.string()),
    pa.field("coherence_score", pa.float64()),
    pa.field("user_correction_count", pa.int64()),
    pa.field("weight_adjustment_signal", pa.float64()),
    pa.field("emergent", pa.bool_()),
    pa.field("frameworks_used", pa.list_(pa.string())),
])

_CRITIC_EVAL_SCHEMA = pa.schema([
    pa.field("response_id", pa.string()),
    pa.field("thread_id", pa.string()),   # foreign key — session that produced this eval
    pa.field("coherence", pa.float64()),
    pa.field("contradiction_detected", pa.bool_()),
    pa.field("drift_risk", pa.float64()),
    pa.field("correction_predicted", pa.bool_()),
    pa.field("notes", pa.string()),
    pa.field("critic_backend", pa.string()),
])

_CONTEXT_STATE_SCHEMA = pa.schema([
    pa.field("session_id", pa.string()),
    pa.field("timestamp", pa.string()),
    pa.field("state_json", pa.string()),   # full ContextState serialized as JSON blob
])

_TUNING_JOB_SCHEMA = pa.schema([
    pa.field("job_id", pa.string()),
    pa.field("triggered_at", pa.string()),
    pa.field("thread_ids_used", pa.list_(pa.string())),
    pa.field("adapter_version_in", pa.int64()),
    pa.field("adapter_version_out", pa.int64()),
    pa.field("approved", pa.bool_()),
    pa.field("composite_signal", pa.float64()),
    pa.field("status", pa.string()),
])


# ---------------------------------------------------------------------------
# DB connection (singleton pattern, lazy init)
# ---------------------------------------------------------------------------

_db: lancedb.DBConnection | None = None


def _get_db() -> lancedb.DBConnection:
    global _db
    if _db is None:
        _db = lancedb.connect(str(_DB_PATH))
    return _db


def _retry(fn, retries: int = 3, backoff: float = 1.5):
    """Retry fn on LanceDB lock errors with exponential backoff."""
    for attempt in range(retries):
        try:
            return fn()
        except Exception as e:
            if "lock" in str(e).lower() and attempt < retries - 1:
                sleep_time = backoff ** attempt
                logger.warning(f"LanceDB lock conflict, retrying in {sleep_time:.1f}s (attempt {attempt+1})")
                time.sleep(sleep_time)
            else:
                raise


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def init_db() -> None:
    """Create LanceDB tables if they don't exist. Safe to call repeatedly."""
    db = _get_db()
    existing = db.table_names()  # NOTE: list_tables() in lancedb>=0.30; table_names() still aliased

    if "thread_deltas" not in existing:
        db.create_table("thread_deltas", schema=_THREAD_DELTA_SCHEMA)
        logger.info("Created table: thread_deltas")

    if "critic_evals" not in existing:
        db.create_table("critic_evals", schema=_CRITIC_EVAL_SCHEMA)
        logger.info("Created table: critic_evals")

    if "context_states" not in existing:
        db.create_table("context_states", schema=_CONTEXT_STATE_SCHEMA)
        logger.info("Created table: context_states")

    if "tuning_jobs" not in existing:
        db.create_table("tuning_jobs", schema=_TUNING_JOB_SCHEMA)
        logger.info("Created table: tuning_jobs")

    logger.info(f"DB initialized at {_DB_PATH}")


def write_delta(delta: ThreadDelta) -> None:
    """Append a ThreadDelta to the context table.

    Only the columns declared in _THREAD_DELTA_SCHEMA are written, so adding
    new dataclass fields (e.g. emergent_detail) does not break existing
    on-disk tables. Such fields still persist fully via the ContextState JSON
    blob (save_context_state), which is what gets restored.
    """
    db = _get_db()
    tbl = db.open_table("thread_deltas")
    record = _serialize_record(delta)
    allowed = set(_THREAD_DELTA_SCHEMA.names)
    record = {k: v for k, v in record.items() if k in allowed}
    _retry(lambda: tbl.add([record]))
    logger.info(f"ThreadDelta written: {delta.thread_id} coherence={delta.coherence_score:.2f}")


def write_critic_eval(eval_: CriticEvaluation, thread_id: str) -> None:
    """Append a CriticEvaluation linked to a thread_id."""
    db = _get_db()
    tbl = db.open_table("critic_evals")
    record = _serialize_record(eval_)
    record["thread_id"] = thread_id
    _retry(lambda: tbl.add([record]))
    logger.info(f"CriticEvaluation written: {eval_.response_id} backend={eval_.critic_backend}")


def load_latest() -> ContextState | None:
    """
    Return the most recent ContextState, or None if no states exist.

    Reconstructs from the stored JSON blob.
    """
    db = _get_db()
    tbl = db.open_table("context_states")
    rows = tbl.to_arrow().to_pylist()
    if not rows:
        return None

    # Sort by timestamp descending, take the first
    rows.sort(key=lambda r: r["timestamp"], reverse=True)
    latest_json = rows[0]["state_json"]

    try:
        data = json.loads(latest_json)
        # Reconstruct nested dataclasses
        from schemas import (CognitiveStyle, PersistentPriors, ThreadDelta,
                             PersonaMemory, PersonaFact, BeliefMemory,
                             DeliberatedBelief)
        style = CognitiveStyle(**data["cognitive_style"])
        priors = PersistentPriors(**data["persistent_priors"])
        deltas = [ThreadDelta(**d) for d in data["thread_deltas"]]

        # Persona is optional — old states predate it, so default to empty.
        persona_raw = data.get("persona") or {}
        persona_facts = []
        for f in persona_raw.get("facts", []):
            f = dict(f)
            if isinstance(f.get("promoted_at"), str):
                try:
                    f["promoted_at"] = datetime.fromisoformat(f["promoted_at"])
                except ValueError:
                    f.pop("promoted_at", None)
            persona_facts.append(PersonaFact(**f))
        persona = PersonaMemory(
            facts=persona_facts,
            cap=int(persona_raw.get("cap", 12)),
        )

        # Beliefs (L2b) are optional too — old states predate them. This is the
        # cross-thread deliberated-belief layer; reconstructing it here is what
        # lets beliefs actually PERSIST and grow session to session.
        beliefs_raw = data.get("beliefs") or {}
        def _belief(b):
            """Reconstruct one DeliberatedBelief, parsing datetime fields and
            tolerating old records that predate the SNR/conflict fields."""
            b = dict(b)
            for dtf in ("formed_at", "last_seen_at"):
                if isinstance(b.get(dtf), str):
                    try:
                        b[dtf] = datetime.fromisoformat(b[dtf])
                    except ValueError:
                        b.pop(dtf, None)
            return DeliberatedBelief(**b)
        belief_list = [_belief(b) for b in beliefs_raw.get("beliefs", [])]
        archived_list = [_belief(b) for b in beliefs_raw.get("archived", [])]
        beliefs = BeliefMemory(
            beliefs=belief_list,
            archived=archived_list,
            cap=int(beliefs_raw.get("cap", 8)),
            merge_threshold=float(beliefs_raw.get("merge_threshold", 0.55)),
            prune_floor=float(beliefs_raw.get("prune_floor", 0.12)),
            conflict_threshold=float(beliefs_raw.get("conflict_threshold", 0.4)),
        )

        return ContextState(
            session_id=data["session_id"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            cognitive_style=style,
            persistent_priors=priors,
            thread_deltas=deltas,
            persona=persona,
            beliefs=beliefs,
        )
    except Exception as e:
        logger.error(f"Failed to reconstruct ContextState: {e}")
        return None


def save_context_state(state: ContextState) -> None:
    """Persist a full ContextState to the context_states table (upsert).

    Upserts by session_id: prior rows for this session are removed so there is
    exactly one current row per session. This prevents stale copies and fixes a
    load-ordering bug where multiple saves within one session shared the same
    state.timestamp (the restore time) and load_latest() could pick an older
    pre-update row. Each save is stamped with the actual write time.
    """
    from datetime import datetime, timezone
    db = _get_db()
    tbl = db.open_table("context_states")
    # Remove any existing rows for this session_id (upsert semantics).
    try:
        _retry(lambda: tbl.delete(f"session_id = '{state.session_id}'"))
    except Exception as e:
        logger.warning(f"Could not delete prior context_state rows: {e}")
    record = {
        "session_id": state.session_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),  # real write time, monotonic
        "state_json": to_json(state),
    }
    _retry(lambda: tbl.add([record]))
    logger.info(f"ContextState saved: {state.session_id}")


def snapshot(state: ContextState, adapter_version: int, base_model: str, notes: str = "") -> SnapshotManifest:
    """
    Write a SnapshotManifest and export current state to snapshots/ directory.

    Returns the manifest for logging.
    """
    from pathlib import Path
    import uuid

    manifest = SnapshotManifest(
        mcm_record_count=len(state.thread_deltas),
        adapter_version=adapter_version,
        base_model=base_model,
        notes=notes,
    )

    snap_dir = Path(__file__).parent / "snapshots"
    snap_dir.mkdir(exist_ok=True)
    snap_file = snap_dir / f"snapshot_{manifest.snapshot_id}.json"

    snap_data = {
        "manifest": _serialize_record(manifest),
        "state": json.loads(to_json(state)),
    }

    snap_file.write_text(json.dumps(snap_data, indent=2))
    logger.info(f"Snapshot written: {snap_file}")
    return manifest


def load_all_deltas() -> list[ThreadDelta]:
    """Return all ThreadDeltas ordered by timestamp ascending."""
    db = _get_db()
    tbl = db.open_table("thread_deltas")
    rows = tbl.to_arrow().to_pylist()
    if not rows:
        return []
    rows.sort(key=lambda r: r["timestamp"])
    result = []
    for row in rows:
        try:
            result.append(ThreadDelta(
                thread_id=row["thread_id"],
                timestamp=datetime.fromisoformat(row["timestamp"]),
                insight_gained=row["insight_gained"],
                coherence_score=float(row["coherence_score"]),
                user_correction_count=int(row["user_correction_count"]),
                weight_adjustment_signal=float(row["weight_adjustment_signal"]),
                emergent=bool(row["emergent"]),
                frameworks_used=list(row["frameworks_used"]),
            ))
        except Exception as e:
            logger.warning(f"Skipping malformed delta row: {e}")
    return result


def load_all_critic_evals() -> list[CriticEvaluation]:
    """Return all stored CriticEvaluation records (append-only, unordered).

    Used by eval.py to compute the *actual* contradiction rate from the
    critic's stored contradiction_detected flag, rather than inferring it
    from coherence dips.
    """
    db = _get_db()
    tbl = db.open_table("critic_evals")
    rows = tbl.to_arrow().to_pylist()
    result = []
    for row in rows:
        try:
            result.append(CriticEvaluation(
                response_id=row.get("response_id", ""),
                coherence=float(row["coherence"]),
                contradiction_detected=bool(row["contradiction_detected"]),
                drift_risk=float(row["drift_risk"]),
                correction_predicted=bool(row["correction_predicted"]),
                notes=row.get("notes", ""),
            ))
        except Exception as e:
            logger.warning(f"Skipping malformed critic_eval row: {e}")
    return result


# ---------------------------------------------------------------------------
# __main__
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    init_db()
    db = _get_db()
    print(f"\nDB path: {_DB_PATH}")
    print(f"Tables: {db.table_names()}")
    for tname in db.table_names():
        tbl = db.open_table(tname)
        print(f"  {tname}: {tbl.count_rows()} rows")
