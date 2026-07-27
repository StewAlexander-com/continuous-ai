#!/usr/bin/env python3
"""Hardening tests for the foreground-priority scheduling feature: gate
singleton thread-safety and observability, drain-mode shutdown bounds, the
token-cap x reasoning-model interaction (think=False request, retry fallback,
<think>-scrubbing fail-safe), pinned-critic survival across a chat-model
switch, and event-log write serialization. Deterministic; no model calls.

Run: ./.venv/bin/python test_gate_hardening.py
"""
import json, logging, sys, threading, time, types
if "ollama" not in sys.modules:
    sys.modules["ollama"] = types.ModuleType("ollama")

import scheduler
from scheduler import ForegroundGate


# ---------- gate hardening ----------

def test_get_gate_is_race_free():
    scheduler._GATE = None
    gates, barrier = [], threading.Barrier(16)
    def grab():
        barrier.wait()
        gates.append(scheduler.get_gate())
    ts = [threading.Thread(target=grab) for _ in range(16)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert len({id(g) for g in gates}) == 1, \
        "racing get_gate() must never mint two gates"
    print("ok: gate singleton survives a 16-thread creation race")


def test_busy_for_s_observability():
    g = ForegroundGate()
    assert g.busy_for_s() == 0.0
    g.begin()
    time.sleep(0.12)
    assert g.busy_for_s() >= 0.1
    g.begin(); g.end()   # nested turn must not reset the busy clock
    assert g.busy_for_s() >= 0.1
    g.end()
    assert g.busy_for_s() == 0.0
    print("ok: busy_for_s tracks the continuous busy stretch")


def test_expired_deferral_is_logged_loudly():
    g = ForegroundGate()
    records = []
    h = logging.Handler(); h.emit = lambda r: records.append(r.getMessage())
    logging.getLogger("scheduler").addHandler(h)
    try:
        g.begin()   # wedged foreground
        g.wait_for_clearance(max_wait=0.1)
        assert any("max deferral" in m for m in records), records
    finally:
        logging.getLogger("scheduler").removeHandler(h)
    print("ok: a wedged/marathon foreground is visible in the log")


# ---------- capped-output scrubbing ----------

def test_scrub_capped_output():
    from session import _scrub_capped_output
    assert _scrub_capped_output("plain answer") == "plain answer"
    assert _scrub_capped_output(
        "<think>step 1... step 2...</think>\nThe answer.") == "The answer."
    # qwen3 + think=False leaks narration with a bare closer (no opening tag),
    # answer after it -- measured live; the narration must be discarded
    assert _scrub_capped_output(
        "Hmm, the user wants an objection... without fluff. </think>  "
        "Streaming increases time-to-first-byte.") == \
        "Streaming increases time-to-first-byte."
    try:
        _scrub_capped_output("pure narration ending at the closer </think>  ")
        assert False, "closer with nothing after it means the answer never came"
    except RuntimeError:
        pass
    for truncated in ("<think>ran out of tok", "<THINK>case-insensitive too",
                      "<think>closed</think><think>then truncated"):
        try:
            _scrub_capped_output(truncated)
            assert False, f"unclosed think must raise: {truncated!r}"
        except RuntimeError:
            pass
    for empty in ("", "   ", "<think>only reasoning, no answer</think>"):
        try:
            _scrub_capped_output(empty)
            assert False, f"empty-after-strip must raise: {empty!r}"
        except RuntimeError:
            pass
    # done_reason discrimination: a capped-out call that never finished a
    # think block is narration (or a lost tail) -- fail safe; the same text
    # from a COMPLETED call is a genuine direct answer -- accept.
    narration = "Hmm, the user wants an objection to the claim about streaming"
    assert _scrub_capped_output(narration, truncated=False) == narration
    try:
        _scrub_capped_output(narration, truncated=True)
        assert False, "capped-out output without a finished think block must raise"
    except RuntimeError:
        pass
    assert _scrub_capped_output("reasoning </think> the verdict.",
                                truncated=True) == "the verdict.", \
        "a finished think block makes even a capped-out call usable"
    print("ok: scrubber strips closed think blocks and fails truncation safely")


# ---------- session wiring ----------

def _fresh_gate():
    scheduler._GATE = None
    return scheduler.get_gate()


def _temp_session(llm_chat, critic_eval=None, **kw):
    import tempfile, storage, mcm as M, session as S
    from pathlib import Path
    tmp = tempfile.mkdtemp(prefix="seedling_hard_")
    storage._DB_PATH = Path(tmp) / "db"; storage._db = None
    m = M.MCM(); m.restore_context(fresh=True)
    critic = types.SimpleNamespace(
        base_model=kw.pop("critic_model", "tiny-critic"),
        evaluate=critic_eval or (lambda u, r: None))
    llm = types.SimpleNamespace(chat=llm_chat)
    kw.setdefault("deliberation_enabled", False)
    kw.setdefault("live_deliberation_enabled", False)
    sess = S.ThreadSession(mcm=m, critic=critic, model_name="m", fresh=True,
                           llm=llm, caution_controller_enabled=False,
                           chain_of_verification_enabled=False, **kw)
    sess.start()
    return tmp, sess


def test_background_call_requests_no_thinking():
    import shutil, storage
    _fresh_gate()
    calls = []
    def llm_chat(model, messages, **kwargs):
        calls.append(kwargs)
        return {"message": {"content": "verdict"}}
    tmp, sess = _temp_session(llm_chat)
    try:
        sess._chat_once_background("m", [{"role": "user", "content": "x"}])
        assert calls[-1].get("think") is False, calls
        print("ok: capped background calls ask the model not to <think>")
    finally:
        shutil.rmtree(tmp, ignore_errors=True); storage._db = None


def test_capped_calls_carry_style_directive_uncapped_do_not():
    import shutil, storage
    _fresh_gate()
    seen = []
    def llm_chat(model, messages, **kwargs):
        seen.append(list(messages))
        return {"message": {"content": "verdict"}}
    tmp, sess = _temp_session(llm_chat)
    try:
        orig = [{"role": "user", "content": "x"}]
        sess._chat_once_background("m", orig)
        assert "hard-truncated" in seen[0][0]["content"], \
            "capped call must lead with the budget style directive"
        assert seen[0][1:] == orig and orig == [{"role": "user", "content": "x"}], \
            "original messages must be preserved, not mutated"
        sess.background_num_predict = 0   # kill switch: pre-feature shape
        sess._chat_once_background("m", orig)
        assert seen[1] == orig, "uncapped call must send messages untouched"
        print("ok: style directive rides only on capped background calls")
    finally:
        shutil.rmtree(tmp, ignore_errors=True); storage._db = None


def test_think_rejection_falls_back_to_plain_call():
    import shutil, storage
    _fresh_gate()
    calls = []
    def llm_chat(model, messages, **kwargs):
        calls.append(dict(kwargs))
        if "think" in kwargs:
            raise Exception('model "m" does not support thinking')
        return {"message": {"content": "verdict"}}
    tmp, sess = _temp_session(llm_chat)
    try:
        out = sess._chat_once_background("m", [{"role": "user", "content": "x"}])
        assert out == "verdict"
        assert len(calls) == 2 and "think" not in calls[1], \
            "must retry exactly once without the think field"
        print("ok: think-rejecting models get one automatic plain retry")
    finally:
        shutil.rmtree(tmp, ignore_errors=True); storage._db = None


def test_backend_without_think_param_falls_back():
    import shutil, storage
    _fresh_gate()
    calls = []
    def llm_chat(model, messages, *, stream=False, options=None, keep_alive=None):
        calls.append(options)
        return {"message": {"content": "verdict"}}
    tmp, sess = _temp_session(llm_chat)
    try:
        out = sess._chat_once_background("m", [{"role": "user", "content": "x"}])
        # the think-carrying call TypeErrors before the fake body runs, so only
        # the successful plain retry is recorded
        assert out == "verdict" and len(calls) == 1, \
            "TypeError from an older backend signature must trigger the fallback"
        print("ok: backends without a think parameter keep working (TypeError path)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True); storage._db = None


def test_truncated_think_trips_deliberation_failsafe():
    """A capped round that dies mid-<think> must surface as machinery failure
    (passthrough) -- NEVER as chain-of-thought fragments stored in a belief."""
    import shutil, storage
    _fresh_gate()
    def llm_chat(model, messages, **kwargs):
        return {"message": {"content": "<think>token budget burned mid-reason"}}
    tmp, sess = _temp_session(llm_chat)
    try:
        from deliberation import deliberate
        d = deliberate("insight under test", "t1",
                       sess._chat_once_background, "m")
        assert d.antithesis == "[deliberation unavailable]", vars(d)
        assert d.synthesis == "insight under test", \
            "insight must pass through unchanged when rounds fail"
        print("ok: mid-think truncation degrades to passthrough, not garbage")
    finally:
        shutil.rmtree(tmp, ignore_errors=True); storage._db = None


def test_drain_mode_skips_gate_wait():
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
        gate.begin()   # simulate a wedged/foreign busy gate at shutdown
        sess._bg_draining = True
        t0 = time.monotonic()
        out = sess._chat_once_background("m", [{"role": "user", "content": "x"}])
        assert out == "r" and time.monotonic() - t0 < 1.0, \
            "draining background call must not wait on a busy gate"
        sess._submit_critic("q", "a")
        assert graded.wait(timeout=5.0), \
            "draining critic grade must not wait on a busy gate"
        sess._join_critic(timeout=5.0)
        gate.end()
        print("ok: drain mode bounds shutdown even with a wedged gate")
    finally:
        shutil.rmtree(tmp, ignore_errors=True); storage._db = None


def test_end_sets_drain_flag():
    import shutil, storage
    _fresh_gate()
    tmp, sess = _temp_session(lambda *a, **k: {"message": {"content": "r"}})
    try:
        assert sess._bg_draining is False
        sess.end()
        assert sess._bg_draining is True, "end() must enter drain mode"
        print("ok: end() flips the session into drain mode")
    finally:
        shutil.rmtree(tmp, ignore_errors=True); storage._db = None


def test_pinned_critic_survives_model_switch():
    import shutil, storage
    _fresh_gate()
    tmp, sess = _temp_session(lambda *a, **k: {"message": {"content": "r"}},
                              critic_model="gemma3:4b")
    try:
        sess.llm.list_models = lambda: []   # skip install/pull checks
        ok, msg = sess.switch_model("other-model", pull_if_missing=False)
        assert ok, msg
        assert sess.critic.base_model == "gemma3:4b", \
            "a deliberately pinned small critic must survive :model"
        assert "pinned" in msg
        # ...but a critic that WAS tracking the chat model still follows it
        # (preserves --model semantics).
        sess.critic.base_model = sess.model_name
        ok, msg = sess.switch_model("third-model", pull_if_missing=False)
        assert ok and sess.critic.base_model == "third-model", msg
        print("ok: :model keeps a pinned critic; a tracking critic still follows")
    finally:
        shutil.rmtree(tmp, ignore_errors=True); storage._db = None


def test_event_log_writes_are_serialized():
    import shutil, storage, tempfile, session as S
    from pathlib import Path
    _fresh_gate()
    tmp, sess = _temp_session(lambda *a, **k: {"message": {"content": "r"}})
    logdir = Path(tempfile.mkdtemp(prefix="seedling_events_"))
    old_dir = S._BUFFER_DIR
    S._BUFFER_DIR = logdir
    try:
        n_threads, n_events = 8, 50
        def writer(i):
            for j in range(n_events):
                sess._log_event("model_call", {"role": f"t{i}", "n": j,
                                               "pad": "x" * 200})
        ts = [threading.Thread(target=writer, args=(i,)) for i in range(n_threads)]
        [t.start() for t in ts]; [t.join() for t in ts]
        files = list(logdir.glob("events_*.jsonl"))
        assert len(files) == 1
        lines = files[0].read_text().splitlines()
        assert len(lines) == n_threads * n_events
        for ln in lines:
            json.loads(ln)   # every line must be intact JSON
        print("ok: concurrent event-log writes never interleave or corrupt")
    finally:
        S._BUFFER_DIR = old_dir
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(logdir, ignore_errors=True)
        storage._db = None


if __name__ == "__main__":
    test_get_gate_is_race_free()
    test_busy_for_s_observability()
    test_expired_deferral_is_logged_loudly()
    test_scrub_capped_output()
    test_background_call_requests_no_thinking()
    test_capped_calls_carry_style_directive_uncapped_do_not()
    test_think_rejection_falls_back_to_plain_call()
    test_backend_without_think_param_falls_back()
    test_truncated_think_trips_deliberation_failsafe()
    test_drain_mode_skips_gate_wait()
    test_end_sets_drain_flag()
    test_pinned_critic_survives_model_switch()
    test_event_log_writes_are_serialized()
    print("\nALL GATE-HARDENING TESTS PASSED")
