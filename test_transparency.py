#!/usr/bin/env python3
"""Tests for the user-facing transparency surfaces: per-turn mechanism trace,
end-of-session 'internal work' summary, and the beliefs view in status. These
show MECHANISM honestly (work started / what happened) and never claim the model
is 'thinking' or conscious. Model-free (mocked); run with the venv.
"""
import sys, types, time
if "ollama" not in sys.modules:
    sys.modules["ollama"] = types.ModuleType("ollama")

import session as S


class _Critic:
    def evaluate(self, u, r):
        return types.SimpleNamespace(coherence=0.8, critic_backend="local")

class _MCM:
    def persona_facts(self): return []
    def promote_persona_fact(self, *a, **k): return "added"
    def resembles_persona_fact(self, t): return False


def _sess():
    import queue, threading, uuid
    s = S.ThreadSession.__new__(S.ThreadSession)
    s.mcm = _MCM(); s.critic = _Critic(); s.model_name = "m"; s.thread_id = str(uuid.uuid4())
    s._messages = [{"role": "system", "content": "SYS"}]
    s._critic_evals = []; s._correction_count = 0; s._memory_notices = []
    s._turn_activity = {"graded": False, "deliberating": False}; s._end_summary = {}
    s._pending_correction = None
    s._critic_q = queue.Queue(); s._critic_worker = None; s._critic_lock = threading.Lock()
    s._history_window_turns = 24; s.deliberation_enabled = True; s.live_deliberation_enabled = True
    s.fresh = False
    s._buffer_critic_eval = lambda e: None; s._log_event = lambda *a, **k: None
    return s


def _patch_ollama(text):
    import ollama
    def chat(model, messages, stream=False, keep_alive=None):
        if stream:
            return iter([{"message": {"content": text}}])
        return {"message": {"content": text}}
    ollama.chat = chat


def test_turn_activity_flags_background_work():
    # A substantive declarative reply -> deliberation submitted -> flag set.
    _patch_ollama("Local-first memory appears to reduce confabulation under sustained load, "
                  "because the model cannot fetch unverifiable external context to invent from.")
    s = _sess()
    # stub the runner so submit() doesn't spin a real thread
    import live_deliberation
    class _R:
        def submit(self, *a, **k): return True
    live_deliberation._RUNNER = _R()
    s.chat("tell me something")
    assert s._turn_activity["graded"] is True
    assert s._turn_activity["deliberating"] is True, "substantive turn should kick off deliberation"
    s._join_critic(timeout=5.0)
    print("ok: per-turn trace flags grading + background deliberation (work STARTED)")


def test_turn_activity_no_deliberation_on_trivial():
    _patch_ollama("ok")     # too short to be a candidate insight
    s = _sess()
    s.chat("hi")
    assert s._turn_activity["graded"] is True
    assert s._turn_activity["deliberating"] is False, "trivial turn should NOT deliberate"
    s._join_critic(timeout=5.0)
    print("ok: trivial turn shows grading only, no deliberation (honest variation)")


def test_summary_separates_user_facts_from_earned_beliefs():
    # The status view must clearly distinguish user-owned facts from model beliefs.
    import tempfile, shutil, storage, mcm as M
    from pathlib import Path
    tmp = tempfile.mkdtemp(prefix="seedling_trans_")
    storage._DB_PATH = Path(tmp) / "db"; storage._db = None
    try:
        m = M.MCM(); m.restore_context(fresh=True)
        m.promote_persona_fact("my name is Stew", "identity", "t1")
        m.promote_belief("Preserving dissent improves coherence.", "Costs latency.", 0.25, True, "t1")
        out = m.summary()
        assert "user-stated, authoritative" in out, "persona must be labeled as user truth"
        assert "model-derived, NOT user facts" in out, "beliefs must be labeled as model-owned"
        assert "signal" in out, "beliefs show their live signal score"
        # honesty: no consciousness language anywhere in the status view
        low = out.lower()
        for banned in ("conscious", "self-aware", "thinking", "sentient", "feels"):
            assert banned not in low, f"status must not imply mind: found {banned!r}"
        print("ok: status separates user facts from earned beliefs; no mind-claims")
    finally:
        shutil.rmtree(tmp, ignore_errors=True); storage._db = None


def test_thinking_indicator_animates_and_erases_cleanly():
    import io, time
    import seedling
    # animates while waiting, then erases itself on stop (no leftover text)
    buf = io.StringIO(); old = sys.stdout; sys.stdout = buf
    sp = seedling._ThinkingIndicator(enabled=True, label="Aida is working")
    sp.start(); time.sleep(0.9); sp.stop(); time.sleep(0.05)
    sys.stdout = old
    out = buf.getvalue()
    assert "Aida is working" in out, "indicator should animate during a real wait"
    assert "\033[2K" in out, "indicator must clear its line on stop"
    assert out.rstrip().endswith("\033[2K") or out.endswith("\033[2K"), "clean erase at end"
    # honesty: no consciousness claim in the label/output
    low = out.lower()
    for banned in ("conscious", "self-aware", "sentient", "feeling"):
        assert banned not in low, f"indicator must not imply mind: {banned!r}"

    # a FAST reply (stopped before the 0.35s threshold) never flashes the label
    buf2 = io.StringIO(); sys.stdout = buf2
    sp2 = seedling._ThinkingIndicator(enabled=True)
    sp2.start(); time.sleep(0.1); sp2.stop(); time.sleep(0.05)
    sys.stdout = old
    assert "working" not in buf2.getvalue(), "fast replies should not flash the indicator"

    # disabled (non-TTY, e.g. piped/logged) => emits nothing
    buf3 = io.StringIO(); sys.stdout = buf3
    sp3 = seedling._ThinkingIndicator(enabled=False)
    sp3.start(); time.sleep(0.5); sp3.stop()
    sys.stdout = old
    assert buf3.getvalue() == "", "disabled indicator must be silent (no spinner in logs)"
    print("ok: thinking indicator animates on wait, erases cleanly, silent on fast/non-tty")


if __name__ == "__main__":
    test_turn_activity_flags_background_work()
    test_turn_activity_no_deliberation_on_trivial()
    test_summary_separates_user_facts_from_earned_beliefs()
    test_thinking_indicator_animates_and_erases_cleanly()
    print("\nALL TRANSPARENCY TESTS PASSED")
