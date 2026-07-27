#!/usr/bin/env python3
"""Tests for foreground-priority scheduling of background model calls:
the ForegroundGate itself, the session wiring (chat marks busy; critic and
background deliberation rounds yield), the token cap on background calls, and
the role-tagged timing instrumentation. Deterministic (fake backends, no
model); thread interactions are synchronized with events, not sleeps-and-hope.

Run: ./.venv/bin/python test_background_priority.py
"""
import sys, threading, time, types
if "ollama" not in sys.modules:
    sys.modules["ollama"] = types.ModuleType("ollama")

import scheduler
from scheduler import ForegroundGate


# ---------- gate unit tests ----------

def test_gate_idle_by_default_and_counted_nesting():
    g = ForegroundGate()
    assert not g.busy()
    assert g.wait_for_clearance(max_wait=5.0) < 0.5   # returns immediately
    g.begin(); g.begin()
    assert g.busy()
    g.end()
    assert g.busy(), "nested begin must keep the gate busy until the last end"
    g.end()
    assert not g.busy()
    # double-end clamps at zero; the next begin still works
    g.end()
    g.begin(); assert g.busy(); g.end(); assert not g.busy()
    print("ok: gate counts nested begin/end and clamps a stray double-end")


def test_gate_blocks_then_releases():
    g = ForegroundGate()
    g.begin()
    released = threading.Event()
    waited_box = {}
    def waiter():
        waited_box["w"] = g.wait_for_clearance(max_wait=10.0)
        released.set()
    t = threading.Thread(target=waiter, daemon=True); t.start()
    time.sleep(0.15)
    assert not released.is_set(), "background must wait while foreground is busy"
    g.end()
    assert released.wait(timeout=5.0)
    assert waited_box["w"] >= 0.1
    print("ok: background call blocks while busy and wakes on end()")


def test_gate_starvation_escape():
    g = ForegroundGate()
    g.begin()   # never ended -- simulates a marathon foreground
    w = g.wait_for_clearance(max_wait=0.2)
    assert 0.15 <= w <= 2.0, f"max deferral must release the call, waited {w}"
    print("ok: max deferral releases a background call even under constant load")


# ---------- session wiring ----------

def _fresh_gate():
    scheduler._GATE = None
    return scheduler.get_gate()


def _temp_session(llm_chat, critic_eval=None, **kw):
    import tempfile, storage, mcm as M, session as S
    from pathlib import Path
    tmp = tempfile.mkdtemp(prefix="seedling_gate_")
    storage._DB_PATH = Path(tmp) / "db"; storage._db = None
    m = M.MCM(); m.restore_context(fresh=True)
    critic = types.SimpleNamespace(
        base_model="tiny-critic",
        evaluate=critic_eval or (lambda u, r: None))
    llm = types.SimpleNamespace(chat=llm_chat)
    kw.setdefault("deliberation_enabled", False)
    kw.setdefault("live_deliberation_enabled", False)
    sess = S.ThreadSession(mcm=m, critic=critic, model_name="m", fresh=True,
                           llm=llm, caution_controller_enabled=False,
                           chain_of_verification_enabled=False, **kw)
    sess.start()
    return tmp, sess


def test_chat_marks_foreground_busy_for_the_whole_turn():
    import shutil, storage
    gate = _fresh_gate()
    seen = {}
    def llm_chat(model, messages, **kwargs):
        seen["busy_during_call"] = gate.busy()
        return {"message": {"content": "a reply of reasonable length for the test."}}
    tmp, sess = _temp_session(llm_chat)
    try:
        assert not gate.busy()
        sess.chat("hello there")
        assert seen["busy_during_call"] is True, \
            "the gate must be busy while the reply is being generated"
        assert not gate.busy(), "gate must be idle again after the turn"
        print("ok: chat() holds the gate for exactly the duration of the turn")
    finally:
        shutil.rmtree(tmp, ignore_errors=True); storage._db = None


def test_background_chat_yields_then_runs_with_token_cap():
    import shutil, storage
    gate = _fresh_gate()
    calls = []
    def llm_chat(model, messages, **kwargs):
        calls.append(kwargs)
        return {"message": {"content": "background verdict"}}
    tmp, sess = _temp_session(llm_chat, background_num_predict=256)
    try:
        gate.begin()   # foreground turn in progress
        done = threading.Event()
        def bg():
            sess._chat_once_background("m", [{"role": "user", "content": "x"}])
            done.set()
        t = threading.Thread(target=bg, daemon=True); t.start()
        time.sleep(0.15)
        assert not calls, "background call must not reach the model while busy"
        gate.end()
        assert done.wait(timeout=5.0)
        assert calls and calls[0].get("options", {}).get("num_predict") == 256, \
            f"background call must be token-capped: {calls}"
        print("ok: background round waits for the foreground, then runs capped")
    finally:
        shutil.rmtree(tmp, ignore_errors=True); storage._db = None


def test_critic_worker_yields_to_foreground():
    import shutil, storage
    gate = _fresh_gate()
    graded = threading.Event()
    def critic_eval(u, r):
        graded.set()
        return types.SimpleNamespace(coherence=0.7, critic_backend="local",
                                     response_id="x", contradiction_detected=False,
                                     drift_risk=0.1, correction_predicted=False,
                                     notes="")
    tmp, sess = _temp_session(lambda *a, **k: {"message": {"content": "r"}},
                              critic_eval=critic_eval)
    try:
        gate.begin()
        sess._submit_critic("question", "answer")
        time.sleep(0.2)
        assert not graded.is_set(), "grade must wait while the foreground is busy"
        gate.end()
        assert graded.wait(timeout=5.0), "grade must proceed once idle"
        sess._join_critic(timeout=5.0)
        print("ok: queued critic grade yields the GPU to the active turn")
    finally:
        shutil.rmtree(tmp, ignore_errors=True); storage._db = None


def test_gate_disabled_restores_old_behavior():
    import shutil, storage
    _fresh_gate()
    calls = []
    def llm_chat(model, messages, **kwargs):
        calls.append(kwargs)
        return {"message": {"content": "r"}}
    tmp, sess = _temp_session(llm_chat, background_gate_enabled=False,
                              background_num_predict=0)
    try:
        assert sess._fg_gate is None
        out = sess._chat_once_background("m", [{"role": "user", "content": "x"}])
        assert out == "r"
        assert "options" not in calls[-1], \
            "cap disabled (0) must not inject options -- exact old call shape"
        print("ok: kill-switch off => ungated, uncapped, pre-change call shape")
    finally:
        shutil.rmtree(tmp, ignore_errors=True); storage._db = None


def test_timing_instrumentation_tags_roles():
    import shutil, storage
    _fresh_gate()
    events = []
    tmp, sess = _temp_session(lambda *a, **k: {"message": {"content": "some reply text here."}})
    try:
        sess._log_event = lambda kind, payload: events.append((kind, payload))
        sess.chat("hi")
        sess._chat_once_background("m", [{"role": "user", "content": "x"}])
        roles = [p["role"] for k, p in events if k == "model_call"]
        assert "chat" in roles and "delib_live" in roles, roles
        rec = next(p for k, p in events
                   if k == "model_call" and p["role"] == "delib_live")
        assert "wait_s" in rec and "call_s" in rec and rec["model"] == "m"
        print("ok: every model call is timing-logged with a role tag")
    finally:
        shutil.rmtree(tmp, ignore_errors=True); storage._db = None


def test_live_deliberation_uses_the_gated_chat_fn():
    """The submit site must hand the runner the BACKGROUND chat fn, so each
    deliberation round re-checks the gate. Verified by intercepting submit."""
    import shutil, storage
    _fresh_gate()
    tmp, sess = _temp_session(
        lambda *a, **k: {"message": {"content":
            "A substantive declarative reply that is comfortably long enough to "
            "clear the live-deliberation candidate filters, with a clear claim: "
            "streaming output reduces perceived latency in interactive systems."}},
        live_deliberation_enabled=True)
    try:
        import live_deliberation as LD
        submitted = {}
        sess_runner = types.SimpleNamespace(
            submit=lambda cand, tid, chat_fn, model:
                submitted.update(chat_fn=chat_fn) or True)
        old = LD.get_runner
        LD._RUNNER = None
        LD.get_runner = lambda: sess_runner
        try:
            sess.chat("tell me a claim")
        finally:
            LD.get_runner = old
        assert submitted.get("chat_fn") == sess._chat_once_background, \
            "deliberation rounds must go through the gated background chat fn"
        print("ok: live deliberation rounds run through the foreground gate")
    finally:
        shutil.rmtree(tmp, ignore_errors=True); storage._db = None


if __name__ == "__main__":
    test_gate_idle_by_default_and_counted_nesting()
    test_gate_blocks_then_releases()
    test_gate_starvation_escape()
    test_chat_marks_foreground_busy_for_the_whole_turn()
    test_background_chat_yields_then_runs_with_token_cap()
    test_critic_worker_yields_to_foreground()
    test_gate_disabled_restores_old_behavior()
    test_timing_instrumentation_tags_roles()
    test_live_deliberation_uses_the_gated_chat_fn()
    print("\nALL BACKGROUND-PRIORITY TESTS PASSED")
