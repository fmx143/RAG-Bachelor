"""Tests for the LLM provider selection logic (manual toggle, persisted in SQLite)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from pydantic import SecretStr

from rag_bachelor.config import settings
from rag_bachelor.core.llm import (
    PROVIDER_SETTING_KEY,
    OllamaProvider,
    OpenAIProvider,
    active_model,
    get_provider,
)
from rag_bachelor.study import store

# ── get_provider() selection ────────────────────────────────────────────────────


def test_defaults_to_ollama_when_nothing_persisted() -> None:
    provider, name = get_provider()
    assert name == "ollama"
    assert isinstance(provider, OllamaProvider)


def test_respects_default_llm_provider_setting() -> None:
    with (
        patch.object(settings, "default_llm_provider", "openai"),
        patch.object(settings, "openai_api_key", SecretStr("sk-test")),
    ):
        provider, name = get_provider()
    assert name == "openai"
    assert isinstance(provider, OpenAIProvider)


def test_persisted_openai_choice_is_used_when_key_configured() -> None:
    store.set_setting(PROVIDER_SETTING_KEY, "openai")
    with patch.object(settings, "openai_api_key", SecretStr("sk-test")):
        provider, name = get_provider()
    assert name == "openai"
    assert isinstance(provider, OpenAIProvider)


def test_openai_choice_falls_back_to_ollama_without_key() -> None:
    """A missing key must never surface as a raw exception — silent, safe fallback."""
    store.set_setting(PROVIDER_SETTING_KEY, "openai")
    with patch.object(settings, "openai_api_key", SecretStr("")):
        provider, name = get_provider()
    assert name == "ollama"
    assert isinstance(provider, OllamaProvider)


def test_persisted_ollama_choice_is_used_even_with_key_configured() -> None:
    store.set_setting(PROVIDER_SETTING_KEY, "ollama")
    with patch.object(settings, "openai_api_key", SecretStr("sk-test")):
        provider, name = get_provider()
    assert name == "ollama"
    assert isinstance(provider, OllamaProvider)


# ── OpenAIProvider ────────────────────────────────────────────────────────────


def test_openai_provider_builds_client_with_configured_key() -> None:
    fake_response = MagicMock()
    fake_response.choices = [MagicMock(message=MagicMock(content="réponse"))]
    with (
        patch.object(settings, "openai_api_key", SecretStr("sk-super-secret")),
        patch("rag_bachelor.core.llm._OpenAI") as mock_openai_cls,
    ):
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = fake_response
        mock_openai_cls.return_value = mock_client

        result = OpenAIProvider().chat([{"role": "user", "content": "salut"}])

    mock_openai_cls.assert_called_once_with(api_key="sk-super-secret")
    assert result == "réponse"


# ── OllamaProvider ────────────────────────────────────────────────────────────


def test_ollama_provider_uses_configured_host_and_model() -> None:
    fake_response = MagicMock()
    fake_response.message.content = "réponse"
    with patch("rag_bachelor.core.llm._ollama.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.chat.return_value = fake_response
        mock_client_cls.return_value = mock_client

        result = OllamaProvider().chat([{"role": "user", "content": "salut"}])

    mock_client_cls.assert_called_once_with(host=settings.ollama_host, headers=None)
    assert result == "réponse"


def test_ollama_client_sends_explicit_bearer_when_key_configured() -> None:
    fake_response = MagicMock()
    fake_response.message.content = "ok"
    with (
        patch.object(settings, "ollama_api_key", SecretStr("ol-secret")),
        patch("rag_bachelor.core.llm._ollama.Client") as mock_client_cls,
    ):
        mock_client_cls.return_value.chat.return_value = fake_response
        OllamaProvider().chat([{"role": "user", "content": "salut"}])

    mock_client_cls.assert_called_once_with(
        host=settings.ollama_host, headers={"Authorization": "Bearer ol-secret"}
    )


def test_persisted_model_overrides_config_default_for_both_providers() -> None:
    assert active_model("ollama") == settings.ollama_model
    assert active_model("openai") == settings.openai_model

    store.set_setting("ollama_model", "gpt-oss:120b")
    store.set_setting("openai_model", "gpt-4o")
    assert active_model("ollama") == "gpt-oss:120b"
    assert active_model("openai") == "gpt-4o"

    fake_response = MagicMock()
    fake_response.message.content = "ok"
    with patch("rag_bachelor.core.llm._ollama.Client") as mock_client_cls:
        mock_client_cls.return_value.chat.return_value = fake_response
        OllamaProvider().chat([{"role": "user", "content": "salut"}])
    assert mock_client_cls.return_value.chat.call_args.kwargs["model"] == "gpt-oss:120b"
