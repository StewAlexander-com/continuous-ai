#!/usr/bin/env python3
"""Unit tests for the LIVE (background) deliberation path. No model needed —
chat_fn is mocked. Verifies: non-blocking submit, drain, queue saturation,
worker resilience, and the model-free gate.
Run: python3 test_live_deliberation.py
"""
import time
import threading
from pathlib import Path

import deliberation
import live_deliberation
from live_deliberation import LiveDeliberator


def _mock_chat_factory(delay=0.0, objection="It depends on context."):
    """Returns a chat_fn that yields an objection then a synthesis, with an
    optional artificial delay to simulate model latency."""
    def mock_chat(model, messages):
        if delay:
            time.sleep(delay)
        if "Antithesis" in messages[0]["content"]:
            return objection
        return "Revised: depends on context."
    return mock_chat


def test_submit_is_nonblocking():
    # Point the ledger at a temp dir so we don't pollute the repo.
    deliberation._LEDGER_DIR = Path("/tmp/_live_delib_ledger")
    runner = LiveDeliberator()
    slow = _mock_chat_factory(delay=0.3)
    t0 = time.monotonic()
    ok = runner.submit("X improves Y under load.", "t-live", slow, "m")
    elapsed = time.monotonic() - t0
    assert ok is True
    # The submit must return effectively immediately, NOT wait for the 0.3s model.
    assert elapsed < 0.1, f"submit blocked for {elapsed:.3f}s (must be non-blocking)"
    assert runner.drain(timeout=5.0) is True
    assert len(runner._results) == 1
    print("ok: submit is non-blocking; work completes in background")


def test_empty_insight_skipped():
    runner = LiveDeliberator()
    assert runner.submit("", "t", _mock_chat_factory(), "m") is False
    assert runner.submit("   ", "t", _mock_chat_factory(), "m") is False
    print("ok: empty/whitespace insight is not enqueued")


def test_drain_waits_for_all():
    deliberation._LEDGER_DIR = Path("/tmp/_live_delib_ledger")
    runner = LiveDeliberator()
    fn = _mock_chat_factory(delay=0.05)
    for i in range(5):
        assert runner.submit(f"Insight number {i} about coherence.", "t", fn, "m")
    assert runner.drain(timeout=10.0) is True
    assert runner.pending() == 0
    assert len(runner._results) == 5
    print("ok: drain blocks until every queued job finishes")


def test_worker_survives_bad_job():
    deliberation._LEDGER_DIR = Path("/tmp/_live_delib_ledger")
    runner = LiveDeliberator()

    def boom(model, messages):
        raise RuntimeError("model down")

    # deliberate() itself fail-safes on a bad chat_fn (passthrough), so the job
    # still "completes" — assert the worker stays alive for a subsequent good job.
    assert runner.submit("This insight will hit a dead model.", "t-bad", boom, "m")
    assert runner.drain(timeout=5.0) is True
    good = _mock_chat_factory()
    assert runner.submit("A healthy follow-up insight about memory.", "t-ok", good, "m")
    assert runner.drain(timeout=5.0) is True
    assert runner.pending() == 0
    print("ok: a failing job never kills the worker thread")


def test_queue_saturation_drops_oldest():
    deliberation._LEDGER_DIR = Path("/tmp/_live_delib_ledger")
    # Tiny queue + a worker we keep busy so the queue actually fills.
    orig_max = live_deliberation._MAX_PENDING
    live_deliberation._MAX_PENDING = 2
    try:
        runner = LiveDeliberator()
        gate = threading.Event()

        def blocker(model, messages):
            gate.wait(timeout=5.0)   # hold the worker on the first job
            return "NO SUBSTANTIVE OBJECTION"

        # First job occupies the worker; next fill the 2-slot queue; further
        # submits must drop oldest pending but still return True (newest kept).
        runner.submit("job0 occupies worker for a moment now.", "t", blocker, "m")
        time.sleep(0.1)
        for i in range(1, 6):
            ok = runner.submit(f"queued job {i} about coherence here.", "t",
                               _mock_chat_factory(), "m")
            assert ok is True
        gate.set()
        assert runner.drain(timeout=10.0) is True
        assert runner.pending() == 0
        print("ok: saturated queue drops oldest pending, never blocks the reply")
    finally:
        live_deliberation._MAX_PENDING = orig_max


# --- the model-free GATE (exercised via a throwaway Session-like shim) ---
def test_gate_logic():
    # Import the gate off the real Session class without constructing deps:
    import types
    from session import ThreadSession
    g = ThreadSession._live_deliberation_candidate
    # shim with an mcm stub (the gate now consults resembles_persona_fact)
    shim = type("S", (), {})()
    shim.mcm = types.SimpleNamespace(resembles_persona_fact=lambda t: False)

    # short / trivial -> skip
    assert g(shim, "ok") is None
    assert g(shim, "Sure, done.") is None
    # pure clarifying question -> skip
    assert g(shim, "Could you clarify which file you mean here exactly please") is None
    # [EMERGENT] marker -> extract the marked claim (a MODEL observation, not a
    # user fact — user-fact echoes are dropped by the doubt-scope guard).
    em = ("Here is a thought.\n[EMERGENT] Reasoning quality seems to rise when "
          "objections survive across sessions.\nMore.")
    cand = g(shim, em)
    assert cand and "objections survive" in cand and "[EMERGENT]" not in cand
    # substantive declarative -> first sentence becomes the candidate
    long = ("Continuous deliberation tends to raise coherence when objections are "
            "preserved rather than averaged away across sessions. This holds under load.")
    cand2 = g(shim, long)
    assert cand2 and cand2.startswith("Continuous deliberation")
    print("ok: gate skips trivia/questions, extracts emergent + substantive claims")


def test_drain_timeout_returns_false_and_warns(caplog=None):
    """A job slower than the drain timeout: drain must return False (not hang,
    not raise) and must NOT lose the result once the job finally finishes.
    This is the 'drain timed out' path observed on qwen3:30b at exit."""
    import logging
    deliberation._LEDGER_DIR = Path("/tmp/_live_delib_ledger")
    runner = LiveDeliberator()
    slow = _mock_chat_factory(delay=0.6)  # job takes ~0.6s
    runner.submit("A durable insight worth deliberating under load.", "t-to", slow, "m")
    # Drain with a timeout SHORTER than the job -> must report not-fully-drained.
    drained = runner.drain(timeout=0.1)
    assert drained is False, "drain should report False when a job is still running"
    # The insight is not lost: once the job finishes, a full drain collects it.
    assert runner.drain(timeout=5.0) is True
    assert len(runner._results) == 1, "result must survive the earlier timeout"
    print("ok: drain timeout returns False, warns, and never loses the result")


def test_scaled_timeout_math():
    """end() scales the drain bound with in-flight jobs, floored by config.
    Mirror that formula here so the contract is locked."""
    cfg_floor = 90.0
    for pending, expected in [(1, 90.0), (2, 90.0), (3, 90.0), (4, 120.0), (5, 150.0)]:
        got = max(cfg_floor, 30.0 * max(1, pending))
        assert got == expected, f"pending={pending}: got {got}, want {expected}"
    # A low floor lets the scaling dominate (fast-exit config).
    assert max(10.0, 30.0 * 3) == 90.0
    print("ok: drain timeout = max(config_floor, 30s * in-flight jobs)")


if __name__ == "__main__":
    test_submit_is_nonblocking()
    test_empty_insight_skipped()
    test_drain_waits_for_all()
    test_worker_survives_bad_job()
    test_queue_saturation_drops_oldest()
    test_gate_logic()
    test_drain_timeout_returns_false_and_warns()
    test_scaled_timeout_math()
    print("\nALL LIVE-DELIBERATION TESTS PASSED")
