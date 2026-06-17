"""Tests for the in-chat ':model' switch (ThreadSession.switch_model + CLI glue).

These cover the LOGIC of the switch with shims -- attribute reassignment, the
chat+critic-together invariant, no-partial-switch on failure, defensive list
parsing, and the CLI number/name resolution. They do NOT load a live model
(no Ollama in CI); the live pull+generate path is validated by hand on the Mac.
"""
import sys
import types
import importlib

import session as session_mod
from session import ThreadSession, _installed_model_names


# --- A minimal critic stand-in: only needs a mutable base_model attribute. ---
class _ShimCritic:
    def __init__(self, base_model):
        self.base_model = base_model


def _make_session(model="qwen2.5:14b", critic_model="qwen2.5:14b"):
    """Build a ThreadSession without running __init__ (no MCM/DB needed).

    switch_model only touches self.model_name, self.critic, self._warmed and
    calls self.warmup(); we set just those, matching the test-shim pattern used
    by the other suites.
    """
    s = ThreadSession.__new__(ThreadSession)
    s.model_name = model
    s.critic = _ShimCritic(critic_model)
    s._warmed = True
    # Neutralize warmup so no network call happens during a switch.
    s.warmup = lambda: None
    return s


def _fake_ollama(installed=None, pull_raises=False, stream_chunks=None,
                 support_stream=True):
    """A fake 'ollama' module.

    list() returns the given tags. pull() can fail, stream progress chunks (when
    called with stream=True), or — if support_stream=False — raise TypeError on
    stream= to exercise the blocking fallback.
    """
    mod = types.SimpleNamespace()
    models = [types.SimpleNamespace(model=name) for name in (installed or [])]
    mod.list = lambda: types.SimpleNamespace(models=models)

    def _pull(name, stream=False):
        if pull_raises:
            raise RuntimeError("simulated pull failure")
        if stream:
            if not support_stream:
                raise TypeError("stream not supported by this client")
            return iter(stream_chunks or [])
        return None
    mod.pull = _pull
    mod.chat = lambda *a, **k: None
    return mod


def _install_fake_ollama(monkey_installed=None, pull_raises=False,
                         stream_chunks=None, support_stream=True):
    sys.modules["ollama"] = _fake_ollama(monkey_installed, pull_raises,
                                         stream_chunks, support_stream)


def _restore_ollama():
    sys.modules.pop("ollama", None)


# ---------------------------------------------------------------------------

def test_switch_reassigns_chat_and_critic_together():
    _install_fake_ollama(["qwen2.5:14b", "qwen2.5:7b"])
    try:
        s = _make_session()
        ok, msg = s.switch_model("qwen2.5:7b")
        assert ok, msg
        assert s.model_name == "qwen2.5:7b", "chat model not switched"
        assert s.critic.base_model == "qwen2.5:7b", "critic model not switched in lockstep"
        assert "qwen2.5:7b" in msg
    finally:
        _restore_ollama()
    print("[PASS] switch reassigns chat + critic together")


def test_switch_to_same_model_is_noop_ok():
    _install_fake_ollama(["qwen2.5:14b"])
    try:
        s = _make_session()
        ok, msg = s.switch_model("qwen2.5:14b")
        assert ok
        assert s.model_name == "qwen2.5:14b"
        assert "Already using" in msg
    finally:
        _restore_ollama()
    print("[PASS] switching to the current model is a safe no-op")


def test_empty_name_rejected_no_change():
    _install_fake_ollama(["qwen2.5:14b"])
    try:
        s = _make_session()
        ok, msg = s.switch_model("")
        assert not ok
        assert s.model_name == "qwen2.5:14b", "state changed on empty input"
        assert "Usage" in msg
    finally:
        _restore_ollama()
    print("[PASS] empty model name is rejected, state intact")


def test_failed_pull_keeps_current_model():
    # 'foo' is not installed and pull fails -> NO partial switch.
    _install_fake_ollama(["qwen2.5:14b"], pull_raises=True)
    try:
        s = _make_session()
        ok, msg = s.switch_model("does-not-exist:99b")
        assert not ok
        assert s.model_name == "qwen2.5:14b", "chat model changed despite failed pull"
        assert s.critic.base_model == "qwen2.5:14b", "critic changed despite failed pull"
        assert "Still using qwen2.5:14b" in msg
    finally:
        _restore_ollama()
    print("[PASS] failed pull leaves the current model fully intact (no partial switch)")


def test_installed_names_parses_object_shape():
    _install_fake_ollama(["a:1", "b:2"])
    try:
        assert _installed_model_names() == ["a:1", "b:2"]
    finally:
        _restore_ollama()
    print("[PASS] _installed_model_names parses the object (.models/.model) shape")


def test_installed_names_parses_dict_shape():
    # Older/dict-style ollama responses: {'models': [{'name': ...}]}
    mod = types.SimpleNamespace()
    mod.list = lambda: {"models": [{"name": "x:1"}, {"model": "y:2"}]}
    sys.modules["ollama"] = mod
    try:
        assert _installed_model_names() == ["x:1", "y:2"]
    finally:
        _restore_ollama()
    print("[PASS] _installed_model_names parses the dict shape")


def test_installed_names_unreachable_returns_empty():
    mod = types.SimpleNamespace()
    def _boom():
        raise RuntimeError("no daemon")
    mod.list = _boom
    sys.modules["ollama"] = mod
    try:
        assert _installed_model_names() == []
    finally:
        _restore_ollama()
    print("[PASS] unreachable Ollama -> empty list (treated as 'unknown', never crashes)")


def test_cli_resolver_number_and_bare(capfdlike=None):
    """The CLI helper resolves a number to the Nth installed model and lists on bare."""
    import seedling
    _install_fake_ollama(["qwen2.5:14b", "llama3.2", "qwen2.5:7b"])
    try:
        s = _make_session(model="qwen2.5:14b")
        # ':model 3' -> third installed tag
        seedling._handle_model_command(s, ":model 3")
        assert s.model_name == "qwen2.5:7b", "number did not resolve to the 3rd model"
        # bare ':model' lists without changing anything
        before = s.model_name
        seedling._handle_model_command(s, ":model")
        assert s.model_name == before, "bare ':model' must not change the model"
    finally:
        _restore_ollama()
    print("[PASS] CLI resolver: ':model N' selects Nth; bare ':model' only lists")


def test_cli_resolver_bad_number_no_change():
    import seedling
    _install_fake_ollama(["qwen2.5:14b", "llama3.2"])
    try:
        s = _make_session(model="qwen2.5:14b")
        seedling._handle_model_command(s, ":model 9")  # out of range
        assert s.model_name == "qwen2.5:14b", "out-of-range number changed the model"
    finally:
        _restore_ollama()
    print("[PASS] CLI resolver: out-of-range number is rejected, no change")


def test_missing_model_pull_reports_progress():
    # 'new:7b' isn't installed; a streamed pull should fire the progress callback
    # with parsed (status, completed, total), then complete the switch.
    chunks = [
        {"status": "pulling manifest"},
        {"status": "downloading", "completed": 25, "total": 100},
        {"status": "downloading", "completed": 100, "total": 100},
        {"status": "success"},
    ]
    _install_fake_ollama(["qwen2.5:14b"], stream_chunks=chunks)
    try:
        s = _make_session()
        seen = []
        ok, msg = s.switch_model("new:7b", progress=lambda st, c, t: seen.append((st, c, t)))
        assert ok, msg
        assert s.model_name == "new:7b"
        assert s.critic.base_model == "new:7b"
        assert ("downloading", 25, 100) in seen, f"progress not reported: {seen}"
        assert ("success", 0, 0) in seen
    finally:
        _restore_ollama()
    print("[PASS] missing-model pull streams progress to the callback, then switches")


def test_pull_without_stream_support_falls_back():
    # Client that raises TypeError on stream= must fall back to a blocking pull
    # and still complete the switch (no crash, no lost progress callback).
    _install_fake_ollama(["qwen2.5:14b"], support_stream=False)
    try:
        s = _make_session()
        ok, msg = s.switch_model("new:7b", progress=lambda *a: None)
        assert ok, msg
        assert s.model_name == "new:7b"
    finally:
        _restore_ollama()
    print("[PASS] pull falls back to blocking when client lacks stream= (no crash)")


def test_cli_progress_silent_on_non_tty():
    """On a non-TTY stdout, the ':model' pull path must emit NO carriage-return
    animation (no '\\r' junk in piped/redirected output). The heads-up + result
    lines may still print; only the live percent line is suppressed."""
    import io
    import contextlib
    import seedling
    chunks = [
        {"status": "downloading", "completed": 50, "total": 100},
        {"status": "success"},
    ]
    _install_fake_ollama(["qwen2.5:14b"], stream_chunks=chunks)
    try:
        s = _make_session()
        buf = io.StringIO()  # StringIO.isatty() is False -> non-TTY path
        with contextlib.redirect_stdout(buf):
            seedling._handle_model_command(s, ":model new:7b")
        out = buf.getvalue()
        assert s.model_name == "new:7b", "switch should still complete on non-TTY"
        assert "\r" not in out, f"carriage-return leaked to non-TTY output: {out!r}"
    finally:
        _restore_ollama()
    print("[PASS] non-TTY output has no carriage-return animation (pipe-safe)")


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
    print(f"\n{len(tests) - failed}/{len(tests)} model-switch checks passed")
    sys.exit(1 if failed else 0)
