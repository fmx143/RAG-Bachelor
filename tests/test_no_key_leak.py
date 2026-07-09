"""Regression tests: LLM SDK error text must never reach the rendered HTML response.

OpenAI SDK auth errors are known to echo back the (invalid) API key in their message —
`answer_question`/`generate_questions` failures must always be caught and replaced with
a generic message, never rendered via ``str(exc)``.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from rag_bachelor.app.web.routes import ask, generate

_FAKE_KEY = "sk-super-secret-should-never-leak-12345"


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(ask.router)
    app.include_router(generate.router)
    return TestClient(app)


def test_ask_error_never_leaks_exception_text(client: TestClient) -> None:
    with patch(
        "rag_bachelor.app.web.routes.ask.answer_question",
        side_effect=RuntimeError(f"Incorrect API key provided: {_FAKE_KEY}"),
    ):
        resp = client.post("/ask", data={"question": "Qu'est-ce qu'un algorithme ?"})

    assert resp.status_code == 200
    assert _FAKE_KEY not in resp.text
    assert "Erreur lors de la génération de la réponse" in resp.text


def test_generate_error_never_leaks_exception_text(client: TestClient) -> None:
    with patch(
        "rag_bachelor.app.web.routes.generate.generate_questions",
        side_effect=RuntimeError(f"Incorrect API key provided: {_FAKE_KEY}"),
    ):
        resp = client.post("/generate", data={"topic": "Complexité algorithmique", "difficulty": "moyen"})

    assert resp.status_code == 200
    assert _FAKE_KEY not in resp.text
    assert "Erreur lors de la génération des questions" in resp.text
