"""Settings routes: per-provider model choice and key-configured gating."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from rag_bachelor.app.web._deps import templates
from rag_bachelor.app.web.routes import settings as settings_routes
from rag_bachelor.config import settings
from rag_bachelor.core.llm import active_model

_FAKE_KEY = "ol-super-secret-should-never-leak"


@pytest.fixture
def client() -> Iterator[TestClient]:
    templates.env.globals.setdefault("auth_enabled", lambda: False)
    app = FastAPI()
    app.include_router(settings_routes.router)
    with (
        patch.object(settings, "ollama_api_key", SecretStr(_FAKE_KEY)),
        patch.object(settings_routes, "_cloud_models", return_value=["gpt-oss:120b"]),
        TestClient(app) as test_client,
    ):
        yield test_client


def test_valid_models_are_persisted(client: TestClient) -> None:
    resp = client.post(
        "/settings/models", data={"ollama_model": " gpt-oss:120b ", "openai_model": "gpt-4o"}
    )

    assert resp.status_code == 200
    assert active_model("ollama") == "gpt-oss:120b"
    assert active_model("openai") == "gpt-4o"


def test_empty_model_is_rejected(client: TestClient) -> None:
    resp = client.post("/settings/models", data={"ollama_model": "  ", "openai_model": "gpt-4o"})

    assert "Nom de modèle invalide" in resp.text
    assert active_model("openai") == settings.openai_model


def test_ollama_provider_needs_a_key(client: TestClient) -> None:
    with patch.object(settings, "ollama_api_key", SecretStr("")):
        resp = client.post("/settings/provider", data={"provider": "ollama"})

    assert "OLLAMA_API_KEY" in resp.text


def test_settings_page_never_renders_the_key_and_lists_cloud_models(client: TestClient) -> None:
    resp = client.get("/settings")

    assert resp.status_code == 200
    assert _FAKE_KEY not in resp.text
    assert '<option value="gpt-oss:120b">' in resp.text
