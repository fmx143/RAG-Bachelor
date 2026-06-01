"""LLM provider abstraction with automatic online/offline detection.

The module exposes a single :func:`get_provider` function that returns an
:class:`LLMProvider` along with its name ("openai" or "ollama").

Selection logic:
  1. If ``settings.force_provider`` is set, always use that provider.
  2. Otherwise: use OpenAI when ``OPENAI_API_KEY`` is set **and** the OpenAI API
     is reachable; fall back to Ollama.

Connectivity is cached for ``settings.connectivity_cache_ttl`` seconds so that
provider selection is fast on subsequent calls within the same Streamlit session.
"""

from __future__ import annotations

import time
from typing import Protocol, runtime_checkable

import httpx
import ollama as _ollama
from openai import OpenAI

from rag_bachelor.config import settings

# ── Protocol ──────────────────────────────────────────────────────────────────


@runtime_checkable
class LLMProvider(Protocol):
    def chat(self, messages: list[dict[str, str]], model: str | None = None) -> str:
        """Send *messages* and return the assistant reply as a string."""
        ...


# ── Connectivity cache ────────────────────────────────────────────────────────


class _ConnectivityCache:
    """Simple TTL cache for the last internet-reachability check."""

    _online: bool | None = None
    _last_check: float = 0.0

    @classmethod
    def is_online(cls) -> bool:
        now = time.monotonic()
        ttl = settings.connectivity_cache_ttl
        if cls._online is None or (now - cls._last_check) > ttl:
            cls._online = cls._probe()
            cls._last_check = now
        return cls._online

    @classmethod
    def _probe(cls) -> bool:
        """Attempt a cheap HEAD to the OpenAI API endpoint."""
        try:
            with httpx.Client(timeout=settings.connectivity_timeout) as client:
                client.head("https://api.openai.com")
            return True
        except Exception:
            return False

    @classmethod
    def invalidate(cls) -> None:
        """Force a fresh probe on the next :meth:`is_online` call."""
        cls._online = None


# ── Provider implementations ──────────────────────────────────────────────────


class OpenAIProvider:
    """Wrapper around the official OpenAI Python client."""

    def __init__(self) -> None:
        self._client = OpenAI(api_key=settings.openai_api_key)

    def chat(self, messages: list[dict[str, str]], model: str | None = None) -> str:
        m = model or settings.openai_model_default
        response = self._client.chat.completions.create(
            model=m,
            messages=messages,  # type: ignore[arg-type]
        )
        return response.choices[0].message.content or ""


class OllamaProvider:
    """Wrapper around the Ollama Python client.

    The Ollama server address is read from ``settings.ollama_host``.  In Docker
    on macOS this should be ``http://host.docker.internal:11434``.
    """

    def chat(self, messages: list[dict[str, str]], model: str | None = None) -> str:
        m = model or settings.ollama_model
        client = _ollama.Client(host=settings.ollama_host)
        response = client.chat(model=m, messages=messages)  # type: ignore[arg-type]
        return response.message.content or ""


# ── Public API ────────────────────────────────────────────────────────────────


def get_provider() -> tuple[LLMProvider, str]:
    """Return ``(provider, name)`` based on config and connectivity.

    *name* is either ``"openai"`` or ``"ollama"``.
    """
    force = settings.force_provider.strip().lower()

    if force == "openai":
        return OpenAIProvider(), "openai"
    if force == "ollama":
        return OllamaProvider(), "ollama"

    # Auto: prefer OpenAI when we have a key and can reach the API
    if settings.openai_api_key and _ConnectivityCache.is_online():
        return OpenAIProvider(), "openai"

    return OllamaProvider(), "ollama"


def is_online() -> bool:
    """Return True if the OpenAI API is currently reachable."""
    return _ConnectivityCache.is_online()
