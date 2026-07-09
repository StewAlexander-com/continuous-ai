"""Unit tests for llm.py — local inference adapter (no live server needed)."""
import sys
from unittest import mock

from llm import (
    OllamaBackend,
    OpenAICompatBackend,
    create_backend_from_config,
    _assert_local_url,
    _normalize_base_url,
)


def test_local_url_guard_rejects_cloud():
    try:
        _assert_local_url("https://api.openai.com/v1")
        assert False, "cloud URL should be rejected"
    except ValueError as e:
        assert "local" in str(e).lower()
    print("[PASS] cloud URLs are blocked")


def test_local_url_accepts_loopback():
    _assert_local_url("http://127.0.0.1:1234/v1")
    _assert_local_url("http://localhost:8080")
    print("[PASS] loopback URLs accepted")


def test_normalize_base_url_appends_v1():
    assert _normalize_base_url("http://127.0.0.1:1234") == "http://127.0.0.1:1234/v1"
    assert _normalize_base_url("http://127.0.0.1:1234/v1") == "http://127.0.0.1:1234/v1"
    print("[PASS] base URL normalization")


def test_create_backend_defaults_to_ollama():
    b = create_backend_from_config({})
    assert b.name == "ollama"
    b2 = create_backend_from_config({"inference_backend": "ollama"})
    assert b2.name == "ollama"
    print("[PASS] default backend is ollama")


def test_create_backend_openai_compat():
    b = create_backend_from_config({
        "inference_backend": "openai_compat",
        "openai_compat_base_url": "http://127.0.0.1:1234",
    })
    assert b.name == "openai_compat"
    assert b.base_url == "http://127.0.0.1:1234/v1"
    print("[PASS] openai_compat backend from config")


def test_ollama_list_models_parses_object_shape():
    import types
    mod = types.SimpleNamespace()
    mod.list = lambda: types.SimpleNamespace(
        models=[types.SimpleNamespace(model="a:1"), types.SimpleNamespace(model="b:2")]
    )
    sys.modules["ollama"] = mod
    try:
        assert OllamaBackend().list_models() == ["a:1", "b:2"]
    finally:
        sys.modules.pop("ollama", None)
    print("[PASS] OllamaBackend list_models")


def test_openai_compat_list_models():
    payload = {"data": [{"id": "local-model"}, {"id": "other"}]}
    backend = OpenAICompatBackend("http://127.0.0.1:9999/v1")

    class FakeResp:
        def raise_for_status(self):
            pass
        def json(self):
            return payload

    class FakeClient:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            pass
        def get(self, url, headers=None):
            assert url.endswith("/models")
            return FakeResp()

    with mock.patch.object(backend, "_client", return_value=FakeClient()):
        assert backend.list_models() == ["local-model", "other"]
    print("[PASS] OpenAICompatBackend list_models")


def test_openai_compat_chat_non_stream():
    backend = OpenAICompatBackend("http://127.0.0.1:9999/v1")
    payload = {"choices": [{"message": {"content": "hello"}}]}

    class FakeResp:
        def raise_for_status(self):
            pass
        def json(self):
            return payload

    class FakeClient:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            pass
        def post(self, url, json=None, headers=None):
            assert url.endswith("/chat/completions")
            assert json["model"] == "m"
            return FakeResp()

    with mock.patch.object(backend, "_client", return_value=FakeClient()):
        out = backend.chat("m", [{"role": "user", "content": "hi"}])
    assert out["message"]["content"] == "hello"
    print("[PASS] OpenAICompatBackend non-stream chat")


def test_openai_compat_chat_stream():
    backend = OpenAICompatBackend("http://127.0.0.1:9999/v1")
    lines = [
        'data: {"choices":[{"delta":{"content":"Hel"}}]}',
        'data: {"choices":[{"delta":{"content":"lo"}}]}',
        "data: [DONE]",
    ]

    class FakeStream:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            pass
        def raise_for_status(self):
            pass
        def iter_lines(self):
            yield from lines

    class FakeClient:
        def __init__(self, *a, **k):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *a):
            pass
        def stream(self, method, url, json=None, headers=None):
            assert method == "POST"
            return FakeStream()

    import httpx
    with mock.patch("httpx.Client", FakeClient):
        chunks = list(
            backend.chat("m", [{"role": "user", "content": "hi"}], stream=True)
        )
    text = "".join(c["message"]["content"] for c in chunks)
    assert text == "Hello"
    print("[PASS] OpenAICompatBackend stream chat")


def test_openai_compat_no_pull():
    backend = OpenAICompatBackend("http://127.0.0.1:1234/v1")
    assert not backend.supports_pull()
    try:
        backend.pull("x")
        assert False, "pull should raise"
    except NotImplementedError:
        pass
    print("[PASS] openai_compat does not support pull")


def test_openai_compat_maps_num_predict():
    assert OpenAICompatBackend._map_options({"num_predict": 128}) == {"max_tokens": 128}
    print("[PASS] option mapping num_predict -> max_tokens")


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
    print(f"\n{len(tests) - failed}/{len(tests)} llm-backend checks passed")
    sys.exit(1 if failed else 0)
