#!/usr/bin/env python3
"""Live (per-turn) deliberation runner — responsiveness-first.

Two-speed design:
  * LIVE path (this module): runs on EVERY turn that produces a durable,
    model-derived candidate insight, but NEVER on the critical path of Aida's
    reply. The reply is returned to the user immediately; deliberation happens
    fire-and-forget on a background daemon thread and lands in the same
    append-only ledger. Responsiveness is paramount during a conversation.
  * END path (deliberation.deliberate, called from session.end): can think
    harder — it is allowed to be slower because the conversation is over.

This module is the execution substrate for the live path. It deliberately
reuses `deliberation.deliberate` verbatim (one tested function, two speeds) and
adds only: a bounded background queue, a single worker thread, and a `drain()`
the end-of-session pass calls so live work finishes before it writes its own
record (no racing the same ledger; the append lock makes interleaving safe
regardless, drain just bounds latency at exit).

SCOPE GUARANTEE (unchanged): only MODEL-DERIVED candidate insights are ever
submitted here. User-anchored facts (directives/corrections) are promoted
verbatim and live, and never enter deliberation. The caller enforces this gate.
"""
from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass

from deliberation import deliberate, Deliberation

logger = logging.getLogger("live_deliberation")

# Bound the queue so a runaway producer can never balloon memory. If full, we
# drop the oldest pending job rather than block the reply path (responsiveness
# wins; the end-of-session pass is the backstop for anything dropped).
_MAX_PENDING = 32


@dataclass
class _Job:
    insight: str
    thread_id: str
    chat_fn: object   # callable(model, messages) -> str
    model: str


class LiveDeliberator:
    """Fire-and-forget background deliberation with a single worker thread.

    One worker (not a pool): deliberation is model-bound and a single local LLM
    serves one request at a time anyway, so additional workers would only queue
    on the model. Serial draining also keeps ledger order intuitive.
    """

    def __init__(self) -> None:
        self._q: "queue.Queue[_Job | None]" = queue.Queue(maxsize=_MAX_PENDING)
        self._worker: threading.Thread | None = None
        self._lock = threading.Lock()
        self._inflight = 0
        self._inflight_lock = threading.Lock()
        self._results: list[Deliberation] = []   # for tests/inspection

    def _ensure_worker(self) -> None:
        with self._lock:
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(
                    target=self._run, name="live-deliberator", daemon=True)
                self._worker.start()

    def submit(self, insight: str, thread_id: str, chat_fn, model: str) -> bool:
        """Queue a candidate insight for background deliberation. Returns False
        if it could not be enqueued (queue saturated). NEVER blocks the caller."""
        insight = (insight or "").strip()
        if not insight:
            return False
        job = _Job(insight, thread_id, chat_fn, model)
        with self._inflight_lock:
            self._inflight += 1
        try:
            self._q.put_nowait(job)
        except queue.Full:
            # Drop the OLDEST pending job to make room; keep the newest. The
            # end-of-session pass still deliberates the final insight, so a
            # dropped mid-conversation candidate is not lost coverage.
            with self._inflight_lock:
                self._inflight -= 1   # this job won't be enqueued
            try:
                dropped = self._q.get_nowait()
                self._q.task_done()
                if dropped is not None:
                    with self._inflight_lock:
                        self._inflight -= 1
                    logger.info("live deliberation queue full; dropped oldest pending job")
                self._q.put_nowait(job)
                with self._inflight_lock:
                    self._inflight += 1
            except queue.Empty:
                return False
        self._ensure_worker()
        return True

    def _run(self) -> None:
        while True:
            job = self._q.get()
            if job is None:                # shutdown sentinel
                self._q.task_done()
                return
            try:
                d = deliberate(job.insight, job.thread_id, job.chat_fn, job.model)
                self._results.append(d)
                logger.info(
                    f"live deliberation done: contested={d.contested} "
                    f"rounds={d.extra.get('rounds')} thread={job.thread_id}")
            except Exception as e:        # never let a bad job kill the worker
                logger.error(f"live deliberation job failed: {e}")
            finally:
                with self._inflight_lock:
                    self._inflight -= 1
                self._q.task_done()

    def pending(self) -> int:
        """Approx jobs queued or in progress (for tests / status)."""
        with self._inflight_lock:
            return self._inflight

    def collect_results(self, timeout: float | None = None) -> list[Deliberation]:
        """Drain in-flight jobs, then return AND CLEAR the accumulated results.
        session.end() calls this to promote each surviving live synthesis into
        the cross-thread belief layer. Safe to call once at end of session."""
        self.drain(timeout=timeout)
        out = list(self._results)
        self._results = []
        return out

    def drain(self, timeout: float | None = None) -> bool:
        """Block until all submitted jobs finish (or timeout). Called by
        session.end() so live deliberations complete before the end pass writes
        its own record. Returns True if fully drained."""
        if self._worker is None:
            return True
        try:
            # queue.join has no timeout; poll instead so end() can bound its wait.
            import time
            deadline = None if timeout is None else (time.monotonic() + timeout)
            while True:
                with self._inflight_lock:
                    if self._inflight <= 0 and self._q.empty():
                        return True
                if deadline is not None and time.monotonic() >= deadline:
                    with self._inflight_lock:
                        still = self._inflight
                    logger.warning(
                        f"live deliberation drain timed out: {still} job(s) still "
                        f"running; their belief promotion is deferred (the insight "
                        f"is still captured in this session's delta, not lost)")
                    return False
                time.sleep(0.05)
        except Exception as e:
            logger.error(f"drain error: {e}")
            return False


# Process-wide singleton: one runner shared across the session.
_RUNNER: LiveDeliberator | None = None


def get_runner() -> LiveDeliberator:
    global _RUNNER
    if _RUNNER is None:
        _RUNNER = LiveDeliberator()
    return _RUNNER
