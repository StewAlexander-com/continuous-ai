"""
llm.py — Thin inference adapter for local LLM backends.

Backends (all local-only; cloud URLs are blocked):
  ollama        — default; uses ollama-python (unchanged behavior for existing users)
  openai_compat — OpenAI-compatible HTTP API (LM Studio, llama.cpp server, vLLM)

The adapter normalizes chat responses to the ollama-python shape so callers
need only swap `ollama.chat(...)` for `backend.chat(...)`.
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from typing import Any, Callable, Iterator
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "0.0.0.0"})

_default_backend: "InferenceBackend | None" = None


def _assert_local_url(url: str) -> None:
    """Reject non-local inference URLs — keeps the runtime offline-by-default."""
    host = (urlparse(url).hostname or "").lower()
    if host not in _LOCAL_HOSTS:
        raise ValueError(
            f"Inference URL must be local (127.0.0.1 / localhost), got host {host!r}. "
            "Cloud endpoints are intentionally blocked."
        )


def _normalize_base_url(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    if not url:
        raise ValueError("openai_compat_base_url is required when inference_backend is openai_compat")
    _assert_local_url(url)
    if not url.endswith("/v1"):
        url = f"{url}/v1"
    return url


def set_default_backend(backend: "InferenceBackend") -> None:
    global _default_backend
    _default_backend = backend


def get_default_backend() -> "InferenceBackend":
    global _default_backend
    if _default_backend is None:
        _default_backend = OllamaBackend()
    return _default_backend


def create_backend_from_config(config: dict) -> "InferenceBackend":
    """Build an inference backend from config.yaml keys."""
    kind = (config.get("inference_backend") or "ollama").strip().lower()
    if kind == "openai_compat":
        return OpenAICompatBackend(
            base_url=config.get("openai_compat_base_url") or "http://127.0.0.1:1234/v1",
            api_key=(config.get("openai_compat_api_key") or "").strip(),
        )
    if kind != "ollama":
        logger.warning("Unknown inference_backend %r; falling back to ollama", kind)
    return OllamaBackend()


class InferenceBackend(ABC):
    """Minimal interface shared by all local inference backends."""

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @abstractmethod
    def chat(
        self,
        model: str,
        messages: list[dict],
        *,
        stream: bool = False,
        options: dict | None = None,
        keep_alive: str | None = None,
    ) -> dict | Iterator[dict]:
        """Chat completion. Non-stream returns ollama-shaped dict; stream yields chunks."""

    @abstractmethod
    def list_models(self) -> list[str]:
        ...

    def probe(self) -> tuple[bool, str]:
        """Best-effort connectivity check. Returns (ok, human-readable detail)."""
        try:
            models = self.list_models()
        except Exception as e:
            return False, str(e)
        if models:
            return True, f"reachable ({len(models)} model{'s' if len(models) != 1 else ''} listed)"
        return True, "reachable (no models listed yet)"

    def friendly_name(self) -> str:
        """Short label for UI copy."""
        if self.name == "ollama":
            return "Ollama"
        url = getattr(self, "base_url", "")
        if ":1234" in url:
            return "LM Studio"
        if ":8080" in url:
            return "llama.cpp server"
        if ":8000" in url:
            return "vLLM"
        return "local OpenAI-compatible server"

    def supports_pull(self) -> bool:
        return False

    def pull(
        self,
        name: str,
        *,
        stream: bool = False,
        progress: Callable[[str, int, int], None] | None = None,
    ) -> None:
        raise NotImplementedError(f"{self.name} does not support model pull")


class OllamaBackend(InferenceBackend):
    """Default backend — wraps ollama-python (existing behavior)."""

    @property
    def name(self) -> str:
        return "ollama"

    def supports_pull(self) -> bool:
        return True

    def list_models(self) -> list[str]:
        return _ollama_list_models()

    def probe(self) -> tuple[bool, str]:
        try:
            import ollama
            ollama.list()
        except Exception as e:
            return False, (
                f"Ollama not reachable — start it with: ollama serve  ({e})"
            )
        models = _ollama_list_models()
        if models:
            n = len(models)
            return True, f"reachable ({n} model{'s' if n != 1 else ''} installed)"
        return True, "reachable (no models pulled yet — run: ollama pull <name>)"

    def pull(
        self,
        name: str,
        *,
        stream: bool = False,
        progress: Callable[[str, int, int], None] | None = None,
    ) -> None:
        import ollama

        if progress is not None:
            try:
                for chunk in ollama.pull(name, stream=True):
                    status = (
                        getattr(chunk, "status", None)
                        or (chunk.get("status") if isinstance(chunk, dict) else "")
                        or ""
                    )
                    _c = getattr(chunk, "completed", None)
                    if _c is None and isinstance(chunk, dict):
                        _c = chunk.get("completed")
                    _t = getattr(chunk, "total", None)
                    if _t is None and isinstance(chunk, dict):
                        _t = chunk.get("total")
                    completed = int(_c) if _c is not None else 0
                    total = int(_t) if _t is not None else 0
                    try:
                        progress(str(status), completed, total)
                    except Exception:
                        pass
                return
            except TypeError:
                pass
        ollama.pull(name)

    def chat(
        self,
        model: str,
        messages: list[dict],
        *,
        stream: bool = False,
        options: dict | None = None,
        keep_alive: str | None = None,
    ) -> dict | Iterator[dict]:
        import ollama

        kw: dict[str, Any] = {}
        if options:
            kw["options"] = options
        if keep_alive:
            kw["keep_alive"] = keep_alive
        return ollama.chat(model=model, messages=messages, stream=stream, **kw)


def _ollama_list_models() -> list[str]:
    try:
        import ollama
        resp = ollama.list()
    except Exception:
        return []
    raw = getattr(resp, "models", None)
    if raw is None and isinstance(resp, dict):
        raw = resp.get("models")
    names: list[str] = []
    for m in (raw or []):
        tag = (
            getattr(m, "model", None)
            or getattr(m, "name", None)
            or (m.get("model") or m.get("name") if isinstance(m, dict) else None)
        )
        if tag:
            names.append(str(tag))
    return names


class OpenAICompatBackend(InferenceBackend):
    """OpenAI-compatible local server (LM Studio, llama.cpp, vLLM)."""

    def __init__(self, base_url: str, api_key: str = "") -> None:
        self.base_url = _normalize_base_url(base_url)
        self.api_key = api_key

    @property
    def name(self) -> str:
        return "openai_compat"

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _client(self):
        import httpx
        return httpx.Client(timeout=httpx.Timeout(600.0, connect=10.0))

    def list_models(self) -> list[str]:
        try:
            with self._client() as client:
                r = client.get(f"{self.base_url}/models", headers=self._headers())
                r.raise_for_status()
                data = r.json()
        except Exception as e:
            logger.debug("openai_compat list_models failed: %s", e)
            return []
        ids: list[str] = []
        for item in data.get("data") or []:
            mid = item.get("id") if isinstance(item, dict) else None
            if mid:
                ids.append(str(mid))
        return ids

    def probe(self) -> tuple[bool, str]:
        try:
            with self._client() as client:
                r = client.get(
                    f"{self.base_url}/models",
                    headers=self._headers(),
                    timeout=5.0,
                )
                r.raise_for_status()
        except Exception as e:
            url = getattr(self, "base_url", "")
            return False, (
                f"{self.friendly_name()} not reachable at {url} — "
                f"start the server and check openai_compat_base_url in config.yaml  ({e})"
            )
        models = self.list_models()
        if models:
            n = len(models)
            return True, f"reachable ({n} model{'s' if n != 1 else ''} loaded)"
        return True, (
            "reachable (no models loaded — open your server UI and load a model first)"
        )

    @staticmethod
    def _map_options(options: dict | None) -> dict[str, Any]:
        if not options:
            return {}
        out: dict[str, Any] = {}
        if "temperature" in options:
            out["temperature"] = options["temperature"]
        if "top_p" in options:
            out["top_p"] = options["top_p"]
        if "num_predict" in options:
            out["max_tokens"] = options["num_predict"]
        return out

    def chat(
        self,
        model: str,
        messages: list[dict],
        *,
        stream: bool = False,
        options: dict | None = None,
        keep_alive: str | None = None,
    ) -> dict | Iterator[dict]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": stream,
            **self._map_options(options),
        }
        url = f"{self.base_url}/chat/completions"
        headers = self._headers()

        if not stream:
            with self._client() as client:
                r = client.post(url, json=payload, headers=headers)
                r.raise_for_status()
                data = r.json()
            content = (
                (data.get("choices") or [{}])[0]
                .get("message", {})
                .get("content", "")
            )
            return {"message": {"content": content or ""}}

        return self._stream_chat(url, payload, headers)

    def _stream_chat(
        self, url: str, payload: dict, headers: dict
    ) -> Iterator[dict]:
        import httpx

        with httpx.Client(timeout=httpx.Timeout(600.0, connect=10.0)) as client:
            with client.stream("POST", url, json=payload, headers=headers) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    body = line[6:].strip()
                    if body == "[DONE]":
                        break
                    try:
                        chunk = json.loads(body)
                    except json.JSONDecodeError:
                        continue
                    delta = (chunk.get("choices") or [{}])[0].get("delta", {})
                    tok = delta.get("content") or ""
                    if tok:
                        yield {"message": {"content": tok}}
