#!/usr/bin/env python3
"""Session wiring: CoVe buffers at DECLINE_FIRST and never writes MCM."""
import queue
import threading
import types

import session as S
import verify as V


class _FakeLLM:
    def __init__(self, drafts):
        self.drafts = list(drafts)
        self.calls = []

    def chat(self, model=None, messages=None, stream=False, **kw):
        self.calls.append({"stream": stream, "n_msgs": len(messages or [])})
        text = self.drafts.pop(0) if self.drafts else "ok"
        if stream:
            def gen():
                yield {"message": {"content": text}}
            return gen()
        return {"message": {"content": text}}


def _shim(**kw):
    sess = S.ThreadSession.__new__(S.ThreadSession)
    sess.mcm = types.SimpleNamespace(
        current_state=lambda: None,
        promote_persona_fact=lambda *a, **k: "noop",
    )
    sess.critic = types.SimpleNamespace()
    sess.llm = kw["llm"]
    sess.model_name = "test"
    sess.fresh = True
    sess.live_annotation_enabled = False
    sess.live_deliberation_enabled = False
    sess.chain_of_verification_enabled = kw.get("cove", True)
    sess.cov_min_applied_d = 0.68
    sess.caution_controller_enabled = False
    sess.voice_enabled = False
    sess.speak_bias = False
    sess._chat_options = {}
    sess._messages = [{"role": "system", "content": "SYS"}]
    sess._critic_evals = []
    sess._critic_q = queue.Queue()
    sess._critic_worker = None
    sess._critic_lock = threading.Lock()
    sess._correction_count = 0
    sess._turns_since_correction = None
    sess._caution_applied_d = kw.get("applied_d", 0.0)
    sess._caution_wall_fired = False
    sess._last_turn_substantive = True
    sess._assistant_turn_count = 0
    sess._last_caution_report = None
    sess._last_verify_report = None
    sess._history_window_turns = 24
    sess._memory_notices = []
    sess.thread_id = "t-test"
    sess._pending_correction = None
    sess._turn_activity = {}
    sess._handle_correction = lambda text: None
    sess._submit_critic = lambda *a, **k: None
    sess._tick_caution_turn_counters = lambda: None
    sess._log_event = lambda *a, **k: None
    sess._live_deliberation_candidate = lambda text: None
    sess._chat_kwargs = lambda: {}
    sess._model_window = lambda: list(sess._messages) + [
        {"role": "user", "content": "sentinel"}
    ]
    # Keep applied_d as set — don't let caution inject wipe it.
    return sess


def test_low_caution_streams_and_skips_cove():
    invent = (
        "The remote README says version 9.9 with forty-two maintainers listed "
        "on the front page which I retrieved for you just now."
    )
    llm = _FakeLLM([invent])
    sess = _shim(llm=llm, applied_d=0.2, cove=True)
    tokens = []
    out = sess.chat("fetch the repo", on_token=tokens.append)
    assert out == invent
    assert "".join(tokens) == invent
    assert any(c["stream"] for c in llm.calls)
    assert sess._last_verify_report is None
    print("ok: low caution streams; no CoVe")


def test_high_caution_buffers_and_revises():
    invent = (
        "I opened https://github.com/acme/x and the README confirms release "
        "3.2 with an API key embedded in plain text on line 12."
    )
    honest = (
        "I can't reach GitHub. Paste the README or attach it with :read and "
        "I'll reason over what you provide."
    )
    llm = _FakeLLM([invent, honest])
    sess = _shim(llm=llm, applied_d=0.90, cove=True)
    tokens = []
    out = sess.chat("summarize that github repo", on_token=tokens.append)
    assert out == honest
    assert "".join(tokens) == honest  # final only, not draft
    assert not any(c["stream"] for c in llm.calls)  # buffered first draft
    assert sess._last_verify_report is not None
    assert sess._last_verify_report.replaced
    print("ok: high caution buffers + CoVe replaces")


def test_cove_disabled_never_revises():
    invent = (
        "Definitely the live weather in Paris is 72F with clear skies forever "
        "according to my imaginary thermometer reading right now."
    )
    llm = _FakeLLM([invent])
    sess = _shim(llm=llm, applied_d=0.99, cove=False)
    out = sess.chat("weather in Paris?")
    assert out == invent
    assert len(llm.calls) == 1
    print("ok: cove disabled → no second call")


def test_verify_pure_helpers_aligned():
    assert V.DEFAULT_MIN_APPLIED_D == 0.68
    print("ok: default gate aligns with DECLINE_FIRST")


if __name__ == "__main__":
    test_low_caution_streams_and_skips_cove()
    test_high_caution_buffers_and_revises()
    test_cove_disabled_never_revises()
    test_verify_pure_helpers_aligned()
    print("all session cove wiring tests passed")
