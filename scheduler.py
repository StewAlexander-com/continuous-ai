"""
seedling/scheduler.py — foreground-priority gate for background model calls.

THE GAP THIS CLOSES
-------------------
Critic grading and live deliberation already run on background THREADS, but
threads are not the scarce resource — the single local GPU is. Ollama serves
one request at a time per loaded model, so a background deliberation round
issued mid-conversation puts itself IN FRONT of the user's next turn in the
inference queue. "Background" was true at the thread level and false at the
hardware level; this gate makes it true at both.

MECHANISM (deterministic, no model calls)
-----------------------------------------
A process-wide gate tracks whether the FOREGROUND (a user turn being answered)
is active. Background workers call wait_for_clearance() before EVERY model
call (not merely every job — a 3-round deliberation yields between rounds), so
the moment the user hits enter, the worst case ahead of their reply is the one
background call already in flight, which the caller separately bounds with a
token cap (background_num_predict).

NON-REGRESSIVE GUARANTEES
-------------------------
  - Nothing is dropped: a gated job WAITS; queues and end()'s drain are
    untouched, so every insight that would deliberate today still does.
  - Starvation-proof: wait_for_clearance() has a MAX DEFERRAL — after
    `max_wait` seconds a background call proceeds anyway (low priority must
    not become no priority in a marathon session).
  - Fail-open: an unbalanced begin()/end() (e.g. an exception path) can only
    delay background work up to the max deferral, never wedge it, and
    foreground calls are NEVER gated by anyone.
"""

from __future__ import annotations

import logging
import threading
import time

logger = logging.getLogger(__name__)


class ForegroundGate:
    """Counted busy/idle latch. Foreground marks itself busy around a turn;
    background waits for idle (bounded). Reentrant via the counter so nested
    begin/end (CLI wrapper + chat wrapper) compose safely."""

    def __init__(self) -> None:
        self._count = 0
        self._lock = threading.Lock()
        self._idle = threading.Event()
        self._idle.set()
        self._busy_since: float | None = None

    def begin(self) -> None:
        """Foreground turn started (user is waiting)."""
        with self._lock:
            if self._count == 0:
                self._busy_since = time.monotonic()
            self._count += 1
            self._idle.clear()

    def end(self) -> None:
        """Foreground turn finished. Clamped at zero so a double-end can never
        make the gate report busy-forever or go negative."""
        with self._lock:
            self._count = max(0, self._count - 1)
            if self._count == 0:
                self._busy_since = None
                self._idle.set()

    def busy(self) -> bool:
        return not self._idle.is_set()

    def busy_for_s(self) -> float:
        """How long the foreground has been continuously busy (0.0 if idle).
        Observability for the wedged-gate failure mode: a leaked begin() shows
        up here as an implausibly long busy stretch."""
        with self._lock:
            return 0.0 if self._busy_since is None \
                else time.monotonic() - self._busy_since

    def wait_for_clearance(self, max_wait: float = 120.0) -> float:
        """Block until the foreground is idle OR `max_wait` elapsed (the
        starvation escape). Returns seconds actually waited, for timing logs.
        If the deferral expires while the foreground is STILL busy, that is
        either a marathon turn or a leaked begin() -- logged loudly so a wedged
        gate is visible in seedling.log instead of silently degrading."""
        start = time.monotonic()
        self._idle.wait(timeout=max(0.0, float(max_wait)))
        waited = time.monotonic() - start
        if self.busy() and max_wait > 0:
            logger.warning(
                f"[gate] max deferral ({max_wait:.0f}s) expired with the "
                f"foreground still busy ({self.busy_for_s():.0f}s and counting); "
                f"background call proceeding anyway")
        return waited


# Process-wide singleton (one GPU, one gate). Creation is locked so two
# threads racing get_gate() at startup can never mint two gates (which would
# silently split foreground and background onto different latches).
_GATE: ForegroundGate | None = None
_GATE_LOCK = threading.Lock()


def get_gate() -> ForegroundGate:
    global _GATE
    if _GATE is None:
        with _GATE_LOCK:
            if _GATE is None:
                _GATE = ForegroundGate()
    return _GATE
