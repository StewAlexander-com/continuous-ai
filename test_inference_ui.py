"""Tests for inference UI commands (:help, :setup, :models alias). No live server."""
import io
import contextlib
import sys
import types

import inputsafe as I
import seedling
from llm import OllamaBackend, OpenAICompatBackend


class _FakeLLM:
    name = "ollama"

    def friendly_name(self):
        return "Ollama"

    def supports_pull(self):
        return True

    def probe(self):
        return True, "reachable (2 models installed)"

    def list_models(self):
        return ["alpha:1", "beta:2"]


def _make_session(model="alpha:1"):
    from session import ThreadSession
    s = ThreadSession.__new__(ThreadSession)
    s.model_name = model
    s.llm = _FakeLLM()
    s.warmup = lambda: None
    return s


def test_normalize_models_alias():
    assert seedling._normalize_model_command(":models") == ":model"
    assert seedling._normalize_model_command(":models 2") == ":model 2"
    assert seedling._normalize_model_command(":model x") == ":model x"
    print("[PASS] :models alias normalizes to :model")


def test_looks_like_command_includes_help_setup():
    assert I.looks_like_command(":help")
    assert I.looks_like_command(":setup")
    assert I.looks_like_command(":models")
    assert I.looks_like_command(":models 2")
    assert not I.looks_like_command("help me")
    print("[PASS] inputsafe recognizes :help, :setup, :models")


def test_help_lists_key_commands():
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        seedling._handle_help_command()
    out = buf.getvalue()
    assert ":setup" in out and ":model" in out and "config.yaml" in out
    print("[PASS] :help lists setup, model, and config guidance")


def test_setup_shows_status():
    buf = io.StringIO()
    config = {"model_name": "alpha:1"}
    with contextlib.redirect_stdout(buf):
        seedling._handle_setup_command(_make_session(), config)
    out = buf.getvalue()
    assert "Ollama" in out and "alpha:1" in out and "OK" in out or "reachable" in out
    print("[PASS] :setup shows backend and model")


def test_model_list_marks_current():
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        seedling._handle_model_command(_make_session(), ":model")
    out = buf.getvalue()
    assert "<- current" in out and "alpha:1" in out
    assert "Session only" in out
    print("[PASS] :model list marks current and notes session-only")


def test_model_unreachable_points_to_setup():
    class _Down(_FakeLLM):
        def probe(self):
            return False, "connection refused"
        def list_models(self):
            return []

    s = _make_session()
    s.llm = _Down()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        seedling._handle_model_command(s, ":model")
    out = buf.getvalue()
    assert ":setup" in out
    print("[PASS] unreachable server suggests :setup")


def test_friendly_name_by_port():
    assert OpenAICompatBackend("http://127.0.0.1:1234").friendly_name() == "LM Studio"
    assert OpenAICompatBackend("http://127.0.0.1:8080").friendly_name() == "llama.cpp server"
    assert OllamaBackend().friendly_name() == "Ollama"
    print("[PASS] friendly backend names")


def test_ollama_probe_unreachable():
    mod = types.SimpleNamespace()
    mod.list = lambda: (_ for _ in ()).throw(RuntimeError("down"))
    sys.modules["ollama"] = mod
    try:
        ok, msg = OllamaBackend().probe()
        assert not ok and "ollama serve" in msg.lower()
    finally:
        sys.modules.pop("ollama", None)
    print("[PASS] Ollama probe reports actionable error")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            failed += 1
            print(f"[FAIL] {t.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"[ERROR] {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} inference-ui checks passed")
    sys.exit(1 if failed else 0)
