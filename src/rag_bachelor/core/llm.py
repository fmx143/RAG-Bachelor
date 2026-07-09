"""LLM provider — local Ollama, or OpenAI if toggled on in Settings."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import ollama as _ollama
from openai import OpenAI as _OpenAI

from rag_bachelor.config import settings
from rag_bachelor.study import store

PROVIDER_SETTING_KEY = "llm_provider"


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
        response = client.chat(model=m, messages=messages)
        return response.message.content or ""


class OpenAIProvider:
    """Wrapper around the OpenAI Python client.

    The key is read via ``get_secret_value()`` only here, at client construction —
    never stored, logged, or passed anywhere else.
    """

    def chat(self, messages: list[dict[str, str]], model: str | None = None) -> str:
        m = model or settings.openai_model
        client = _OpenAI(api_key=settings.openai_api_key.get_secret_value())
        response = client.chat.completions.create(model=m, messages=messages)  # type: ignore[arg-type]
        return response.choices[0].message.content or ""


def get_provider() -> tuple[LLMProvider, str]:
    """Return the active provider per the Settings toggle, falling back to Ollama.

    Falls back when OpenAI is selected but no key is configured, so a missing key
    never surfaces as a raw exception to the user.
    """
    choice = store.get_setting(PROVIDER_SETTING_KEY, settings.default_llm_provider)
    if choice == "openai" and settings.openai_api_key.get_secret_value():
        return OpenAIProvider(), "openai"
    return OllamaProvider(), "ollama"
