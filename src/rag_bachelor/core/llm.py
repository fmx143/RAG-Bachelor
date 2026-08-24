"""LLM provider — local Ollama, or OpenAI if toggled on in Settings."""

from __future__ import annotations

import base64
from typing import Protocol, runtime_checkable

import ollama as _ollama
from openai import OpenAI as _OpenAI

from rag_bachelor.config import settings
from rag_bachelor.study import store

PROVIDER_SETTING_KEY = "llm_provider"
VISION_SETTING_KEY = "vision_captioning_enabled"


@runtime_checkable
class LLMProvider(Protocol):
    def chat(
        self, messages: list[dict[str, str]], model: str | None = None, json_mode: bool = False
    ) -> str:
        """Send *messages* and return the assistant reply as a string.

        *json_mode* asks the provider to constrain output to a single valid
        JSON object — the tolerant parsing in ``core/qtypes.py`` stays the
        safety net, not the primary mechanism.
        """
        ...

    def caption(self, image_png: bytes, prompt: str) -> str:
        """Describe *image_png* per *prompt* and return the reply as a string."""
        ...


class OllamaProvider:
    """Wrapper around the Ollama Python client."""

    def chat(
        self, messages: list[dict[str, str]], model: str | None = None, json_mode: bool = False
    ) -> str:
        m = model or settings.ollama_model
        client = _ollama.Client(host=settings.ollama_host)
        response = client.chat(model=m, messages=messages, format="json" if json_mode else None)
        return response.message.content or ""

    def caption(self, image_png: bytes, prompt: str) -> str:
        client = _ollama.Client(host=settings.ollama_host)
        response = client.chat(
            model=settings.ollama_vision_model,
            messages=[{"role": "user", "content": prompt, "images": [image_png]}],
        )
        return response.message.content or ""


class OpenAIProvider:
    """Wrapper around the OpenAI Python client.

    The key is read via ``get_secret_value()`` only here, at client construction —
    never stored, logged, or passed anywhere else.
    """

    def chat(
        self, messages: list[dict[str, str]], model: str | None = None, json_mode: bool = False
    ) -> str:
        m = model or settings.openai_model
        client = _OpenAI(api_key=settings.openai_api_key.get_secret_value())
        if json_mode:
            response = client.chat.completions.create(  # type: ignore[call-overload]
                model=m,
                messages=messages,
                response_format={"type": "json_object"},
            )
        else:
            response = client.chat.completions.create(model=m, messages=messages)  # type: ignore[arg-type]
        return response.choices[0].message.content or ""

    def caption(self, image_png: bytes, prompt: str) -> str:
        client = _OpenAI(api_key=settings.openai_api_key.get_secret_value())
        b64 = base64.b64encode(image_png).decode()
        response = client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                    ],
                }
            ],
        )
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
