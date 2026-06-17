#!/usr/bin/env python3
"""Tests for the responsiveness work: background critic, token streaming, and a
bounded history window. Mocks the model + critic so no Ollama/DB is needed.
Run: ./.venv/bin/python test_responsiveness.py  (or system python3 if deps present)
"""
import sys
import time
import types

# --- stub the heavy/optional deps BEFORE importing session ---------------
# ollama is monkeypatched per-test; provide a placeholder module so import works.
if "ollama" not in sys.modules:
    sys.modules["ollama"] = types.ModuleType("ollama")

import session as S


class _FakeCritic:
    """Critic stub whose evaluate() is slow, so we can prove chat() does NOT
    wait for it."""
    def __init__(self, delay=0.3):
        self.delay = delay
        self.calls = 0

    def evaluate(self, user_query, model_response):
        time.sleep(self.delay)
        self.calls += 1
        return types.SimpleNamespace(coherence=0.8, critic_backend="local")


class _FakeMCM:
    """MCM stub: no persona facts, no real promotion, no DB."""
    def persona_facts(self):
        return []
    def promote_persona_fact(self, *a, **k):
        return "added"
    def promote_belief(self, *a, **k):
        return "added"


def _make_session(critic_delay=0.3, window=6):
    sess = S.ThreadSession.__new__(S.ThreadSession)   # bypass __init__ heavy deps
    # minimal state the methods touch:
    import queue, threading, uuid
    sess.mcm = _FakeMCM()
    sess.critic = _FakeCritic(delay=critic_delay)
    sess.model_name = "fake-model"
    sess.thread_id = str(uuid.uuid4())
    sess._messages = [{"role": "system", "content": "SYS"}]
    sess._critic_evals = []
    sess._correction_count = 0
    sess._memory_notices = []
    sess._pending_correction = None
    sess._critic_q = queue.Queue()
    sess._critic_worker = None
    sess._critic_lock = threading.Lock()
    sess._history_window_turns = window
    sess.deliberation_enabled = False        # isolate the perf path under test
    sess.live_deliberation_enabled = False
    sess.fresh = False
    # buffer write is a no-op for the test
    sess._buffer_critic_eval = lambda e: None
    sess._log_event = lambda *a, **k: None
    return sess


def _patch_ollama(monkey_response="Hello there, this is a reply.", tokens=None, delay=0.0):
    """Install a fake ollama.chat supporting both stream and non-stream."""
    import ollama
    def chat(model, messages, stream=False, keep_alive=None):
        if stream:
            toks = tokens or ["Hel", "lo ", "there."]
            def gen():
                for t in toks:
                    if delay:
                        time.sleep(delay)
                    yield {"message": {"content": t}}
            return gen()
        if delay:
            time.sleep(delay)
        return {"message": {"content": monkey_response}}
    ollama.chat = chat


def test_critic_is_off_reply_path():
    _patch_ollama(monkey_response="A reply.")
    sess = _make_session(critic_delay=0.4)
    t0 = time.monotonic()
    out = sess.chat("hello")          # non-streaming path
    elapsed = time.monotonic() - t0
    assert out == "A reply."
    # chat() must return WITHOUT waiting for the 0.4s critic.
    assert elapsed < 0.2, f"chat() blocked {elapsed:.3f}s on the critic"
    # eval has not necessarily landed yet; join and confirm it does.
    sess._join_critic(timeout=5.0)
    assert len(sess._critic_evals) == 1 and sess.critic.calls == 1
    print("ok: critic runs in background; reply returns immediately; eval still lands")


def test_streaming_emits_tokens_and_returns_full():
    _patch_ollama(tokens=["The ", "quick ", "brown ", "fox."])
    sess = _make_session()
    seen = []
    out = sess.chat("hi", on_token=lambda t: seen.append(t))
    assert seen == ["The ", "quick ", "brown ", "fox."], seen
    assert out == "The quick brown fox.", out
    sess._join_critic(timeout=5.0)
    assert len(sess._critic_evals) == 1   # critic still graded the streamed turn
    print("ok: streaming emits tokens incrementally AND returns the full string")


def test_history_window_bounds_model_input_but_not_memory():
    _patch_ollama(monkey_response="ok")
    sess = _make_session(window=4)
    captured = {}
    import ollama
    orig = ollama.chat
    def spy(model, messages, stream=False, keep_alive=None):
        captured["sent"] = list(messages)
        return orig(model, messages, stream=stream, keep_alive=keep_alive)
    ollama.chat = spy
    # simulate a long prior transcript: system + 10 turns
    for i in range(10):
        sess._messages.append({"role": "user" if i % 2 == 0 else "assistant",
                               "content": f"m{i}"})
    sess.chat("newest")
    sent = captured["sent"]
    # system prompt always kept; window caps the rest at 4 (the newest tail)
    # System prompt always kept (the operational voice may APPEND an implicit
    # tone line to the SENT copy; the stored prompt is unchanged), so check the
    # sent system message STARTS WITH the original content rather than ==.
    assert sent[0]["content"].startswith("SYS"), "system prompt must always be sent"
    assert len(sent) <= 1 + 4 + 1, f"window not bounded: {len(sent)} msgs"
    assert sent[-1]["content"] == "newest", "newest turn must be included"
    # full memory is NOT truncated:
    assert any(m["content"] == "m0" for m in sess._messages), "full transcript kept in memory"
    print("ok: model sees only the recent window; full transcript stays in memory")


def test_correction_still_short_circuits_no_model_no_critic():
    # A persona fact exists and the user issues a correction -> handled in code,
    # no model call, no critic submitted (logic unchanged by the perf work).
    _patch_ollama(monkey_response="SHOULD NOT BE CALLED")
    sess = _make_session()
    called = {"model": False}
    import ollama
    def boom(*a, **k):
        called["model"] = True
        return {"message": {"content": "x"}}
    ollama.chat = boom
    # give it a persona fact + a clear correction
    class _MCM2(_FakeMCM):
        def persona_facts(self):
            return [types.SimpleNamespace(text="your name is Bob", kind="identity")]
        def match_persona_fact(self, q, threshold=0.07):
            return 0
        def remove_persona_fact(self, i):
            return types.SimpleNamespace(text="your name is Bob", kind="identity")
    sess.mcm = _MCM2()
    out = sess.chat("that's wrong, the correct name is Aida not Bob")
    assert out.startswith("[memory"), out
    assert called["model"] is False, "correction must NOT call the model"
    assert sess._critic_worker is None, "correction must NOT spin up the critic"
    print("ok: memory correction short-circuits — no model, no critic (no regression)")


if __name__ == "__main__":
    test_critic_is_off_reply_path()
    test_streaming_emits_tokens_and_returns_full()
    test_history_window_bounds_model_input_but_not_memory()
    test_correction_still_short_circuits_no_model_no_critic()
    print("\nALL RESPONSIVENESS TESTS PASSED")
