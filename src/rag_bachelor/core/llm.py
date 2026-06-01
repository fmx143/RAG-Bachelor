"""LLM provider — Ollama only."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import ollama as _ollama

from rag_bachelor.config import settings


@runtime_checkable
class LLMProvider(Protocol):
    def chat(self, messages: list[dict[str, str]], model: str | None = None) -> str:
        """Send *messages* and return the assistant reply as a string."""
        ...


class OllamaProvider:
    """Wrapper around the Ollama Python client."""

    def chat(self, messages: list[dict[str, str]], model: str | None = None) -> str:
        m = model or settings.ollama_model
        client = _ollama.Client(host=settings.ollama_host)
        response = client.chat(model=m, messages=messages)  # type: ignore[arg-type]
        return response.message.content or ""


def get_provider() -> tuple[LLMProvider, str]:
    """Return the Ollama provider."""
    return OllamaProvider(), "ollama"
