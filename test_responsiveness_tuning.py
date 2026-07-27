#!/usr/bin/env python3
"""Tests for the responsiveness tuning: chat_options plumbing + warmup, verified
to be ZERO-CHANGE by default (no options sent unless configured). Model-free.
Run: ./.venv/bin/python test_responsiveness_tuning.py
"""
import sys, types
if "ollama" not in sys.modules:
    sys.modules["ollama"] = types.ModuleType("ollama")

import session as S
import seedling


def _bare_session(chat_options=None):
    import queue, threading, uuid
    s = S.ThreadSession.__new__(S.ThreadSession)
    s.model_name = "m"; s.thread_id = uuid.uuid4().hex
    s.chat_options = dict(chat_options) if chat_options else {}
    s._warmed = False
    return s


def test_default_options_are_zero_change():
    # No config => empty options => _chat_kwargs sends ONLY keep_alive (no
    # 'options' key), so the model's own defaults are untouched.
    s = _bare_session()
    kw = s._chat_kwargs()
    assert kw == {"keep_alive": "30m"}, kw
    assert "options" not in kw
    print("ok: default (unset) options => no 'options' sent => zero behavior change")


def test_configured_options_are_passed():
    s = _bare_session({"num_predict": 512, "num_ctx": 8192})
    kw = s._chat_kwargs()
    assert kw["options"] == {"num_predict": 512, "num_ctx": 8192}
    assert kw["keep_alive"] == "30m"
    print("ok: configured chat_options are forwarded to ollama")


def test_config_helper_filters_to_known_keys():
    # only allowed keys pass; unset/None/unknown are dropped
    cfg = {"chat_options": {"num_predict": 256, "bogus": 1, "num_ctx": None, "temperature": 0.2}}
    opts = seedling._chat_options_from_config(cfg)
    assert opts == {"num_predict": 256, "temperature": 0.2}, opts
    # missing block => empty
    assert seedling._chat_options_from_config({}) == {}
    print("ok: config helper keeps only known, set option keys")


def test_warmup_is_best_effort_and_idempotent():
    import ollama
    calls = {"n": 0}
    def chat(model, messages, keep_alive=None, options=None, stream=False):
        calls["n"] += 1
        return {"message": {"content": "ok"}}
    ollama.chat = chat
    s = _bare_session()
    s.warmup()
    assert s._warmed is True and calls["n"] == 1
    s.warmup()                       # idempotent: no second call
    assert calls["n"] == 1
    # failure path: warmup must never raise
    def boom(*a, **k): raise RuntimeError("model down")
    ollama.chat = boom
    s2 = _bare_session()
    s2.warmup()                      # should swallow the error
    assert s2._warmed is False
    print("ok: warmup is best-effort (never raises) and idempotent")


if __name__ == "__main__":
    test_default_options_are_zero_change()
    test_configured_options_are_passed()
    test_config_helper_filters_to_known_keys()
    test_warmup_is_best_effort_and_idempotent()
    print("\nALL RESPONSIVENESS-TUNING TESTS PASSED")
