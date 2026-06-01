"""Tests for the LLM provider selection logic."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from rag_bachelor.core import llm as llm_mod
from rag_bachelor.core.llm import (
    OllamaProvider,
    OpenAIProvider,
    _ConnectivityCache,
    get_provider,
)


@pytest.fixture(autouse=True)
def reset_cache() -> None:
    """Reset the connectivity cache before each test so tests are isolated."""
    _ConnectivityCache.invalidate()
    yield
    _ConnectivityCache.invalidate()


# ── Force provider ────────────────────────────────────────────────────────────


def test_force_openai_ignores_connectivity() -> None:
    with patch.object(llm_mod.settings, "force_provider", "openai"):
        with patch.object(llm_mod.settings, "openai_api_key", "sk-test"):
            provider, name = get_provider()
    assert name == "openai"
    assert isinstance(provider, OpenAIProvider)


def test_force_ollama_ignores_connectivity() -> None:
    with patch.object(llm_mod.settings, "force_provider", "ollama"):
        provider, name = get_provider()
    assert name == "ollama"
    assert isinstance(provider, OllamaProvider)


def test_force_provider_case_insensitive() -> None:
    with patch.object(llm_mod.settings, "force_provider", "OLLAMA"):
        provider, name = get_provider()
    assert name == "ollama"


# ── Auto detection ────────────────────────────────────────────────────────────


def test_auto_online_with_key_returns_openai() -> None:
    import time

    # Pre-populate with a fresh cache hit so no network probe is triggered
    _ConnectivityCache._online = True
    _ConnectivityCache._last_check = time.monotonic()

    with patch.object(llm_mod.settings, "force_provider", ""):
        with patch.object(llm_mod.settings, "openai_api_key", "sk-test"):
            with patch.object(llm_mod.settings, "connectivity_cache_ttl", 3600):
                provider, name = get_provider()

    assert name == "openai"
    assert isinstance(provider, OpenAIProvider)


def test_auto_offline_returns_ollama() -> None:
    with patch.object(llm_mod.settings, "force_provider", ""):
        with patch.object(llm_mod.settings, "openai_api_key", "sk-test"):
            _ConnectivityCache._online = False
            provider, name = get_provider()
    assert name == "ollama"
    assert isinstance(provider, OllamaProvider)


def test_auto_online_no_key_returns_ollama() -> None:
    """Even with internet, fall back to Ollama when there is no API key."""
    with patch.object(llm_mod.settings, "force_provider", ""):
        with patch.object(llm_mod.settings, "openai_api_key", ""):
            _ConnectivityCache._online = True
            provider, name = get_provider()
    assert name == "ollama"
    assert isinstance(provider, OllamaProvider)


# ── Connectivity cache ────────────────────────────────────────────────────────


def test_connectivity_cache_ttl_is_respected() -> None:
    """Within the TTL, is_online() should return the cached value without re-probing."""
    import time

    # Pre-populate cache with True just now
    _ConnectivityCache._online = True
    _ConnectivityCache._last_check = time.monotonic()

    # Set a long TTL so the cache is definitely still fresh
    with patch.object(llm_mod.settings, "connectivity_cache_ttl", 3600):
        result = _ConnectivityCache.is_online()

    # Should return the cached True value without hitting the network
    assert result is True


def test_connectivity_invalidate_forces_reprobe() -> None:
    """After invalidate(), the next is_online() should re-probe."""
    _ConnectivityCache._online = True
    _ConnectivityCache.invalidate()
    assert _ConnectivityCache._online is None
