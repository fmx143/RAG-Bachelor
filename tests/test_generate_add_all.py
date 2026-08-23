"""POST /generate/add-all: bulk-add a generation batch to the revision deck."""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from rag_bachelor.app.web.routes import generate
from rag_bachelor.study import store


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(generate.router)
    return TestClient(app)


def test_add_all_inserts_every_valid_item(client: TestClient) -> None:
    items = [
        {"question": "Q1 ?", "answer": "R1.", "difficulty": "facile"},
        {
            "question": "Q2 ?",
            "answer": "R2.",
            "difficulty": "difficile",
            "qtype": "mcq_single",
            "options": ["A", "B"],
            "correct": [1],
        },
    ]
    resp = client.post(
        "/generate/add-all",
        data={"questions_json": json.dumps(items), "topic": "Sujet"},
    )
    assert resp.status_code == 200
    assert "2 carte(s) ajoutée(s)" in resp.text
    assert len(store.get_all_cards()) == 2


def test_add_all_skips_blank_and_malformed_items(client: TestClient) -> None:
    items = [{"question": "  ", "answer": "R."}, "not-a-dict", {"question": "Q ?"}]
    resp = client.post(
        "/generate/add-all",
        data={"questions_json": json.dumps(items), "topic": "Sujet"},
    )
    assert resp.status_code == 200
    assert "1 carte(s) ajoutée(s)" in resp.text
    assert len(store.get_all_cards()) == 1


def test_add_all_tolerates_invalid_json(client: TestClient) -> None:
    resp = client.post(
        "/generate/add-all",
        data={"questions_json": "{not valid json", "topic": "Sujet"},
    )
    assert resp.status_code == 200
    assert "0 carte(s) ajoutée(s)" in resp.text
    assert store.get_all_cards() == []


def test_add_all_tolerates_malformed_options_shape(client: TestClient) -> None:
    """A tampered options/correct field (wrong shape, not a list) must degrade
    to an unstructured card, not raise when the template later iterates it."""
    items = [
        {
            "question": "Q ?",
            "answer": "R.",
            "qtype": "mcq_single",
            "options": "not-a-list",
            "correct": 5,
        }
    ]
    resp = client.post(
        "/generate/add-all",
        data={"questions_json": json.dumps(items), "topic": "Sujet"},
    )
    assert resp.status_code == 200
    cards = store.get_all_cards()
    assert len(cards) == 1
    assert cards[0].options is None
    assert cards[0].correct is None


def test_add_single_tolerates_malformed_options_json(client: TestClient) -> None:
    """A malformed options/correct field must degrade to an unstructured card, not 500."""
    resp = client.post(
        "/generate/add",
        data={
            "question": "Q ?",
            "answer": "R.",
            "qtype": "mcq_single",
            "options": "{not valid json",
            "correct": "[0",
        },
    )
    assert resp.status_code == 200
    cards = store.get_all_cards()
    assert len(cards) == 1
    assert cards[0].options is None
    assert cards[0].correct is None
