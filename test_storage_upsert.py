"""Crash-safe context_state upsert + UUID-safe delete predicates."""
import sys
import uuid

sys.path.insert(0, ".")
import storage as St


def test_session_id_pred_rejects_injection():
    try:
        St._session_id_delete_pred("'; DROP TABLE context_states;--")
        assert False, "should reject non-UUID"
    except ValueError as e:
        assert "uuid" in str(e).lower() or "refusing" in str(e).lower()
    try:
        St._session_id_delete_pred("not-a-uuid")
        assert False, "should reject non-UUID"
    except ValueError:
        pass
    sid = str(uuid.uuid4())
    pred = St._session_id_delete_pred(sid)
    assert pred == f"session_id = '{sid}'"
    pred2 = St._session_id_delete_pred(sid, and_older_than="2026-01-01T00:00:00+00:00")
    assert "AND timestamp <" in pred2
    print("[PASS] session_id delete predicate is UUID-safe")


def test_save_context_state_add_then_prune(tmp_path=None):
    """Add-first upsert: after save, exactly one row for the session (or newest wins)."""
    import tempfile
    from pathlib import Path
    from schemas import ContextState

    tmp = Path(tempfile.mkdtemp(prefix="seedling_upsert_"))
    old_path, old_db = St._DB_PATH, St._db
    St._DB_PATH = tmp / "db"
    St._db = None
    try:
        St.init_db()
        state = ContextState()
        sid = state.session_id
        St.save_context_state(state)
        St.save_context_state(state)  # second save should prune older
        tbl = St._get_db().open_table("context_states")
        rows = [r for r in tbl.to_arrow().to_pylist() if r["session_id"] == sid]
        assert len(rows) == 1, f"expected 1 row after upsert, got {len(rows)}"
        loaded = St.load_latest()
        assert loaded is not None and loaded.session_id == sid
        print("[PASS] add-then-prune upsert leaves one row / load_latest ok")
    finally:
        St._DB_PATH = old_path
        St._db = old_db


if __name__ == "__main__":
    test_session_id_pred_rejects_injection()
    test_save_context_state_add_then_prune()
    print("\nALL STORAGE UPSERT TESTS PASSED")
    sys.exit(0)
