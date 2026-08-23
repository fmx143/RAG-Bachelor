"""POST /revision/answer/{card_id}: deterministic auto-grading for structured cards."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from rag_bachelor.app.web.routes import revision
from rag_bachelor.study import store


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(revision.router)
    return TestClient(app)


def _mcq_card_id() -> int:
    return store.add_card(
        question="2 + 2 = ?",
        answer="4",
        topic="Maths",
        difficulty="facile",
        qtype="mcq_single",
        options=["3", "4", "5"],
        correct=[1],
    )


def test_correct_selection_auto_grades_and_advances_card(client: TestClient) -> None:
    card_id = _mcq_card_id()
    before = next(c for c in store.get_all_cards() if c.id == card_id)

    resp = client.post(f"/revision/answer/{card_id}", data={"selected": ["1"]})

    assert resp.status_code == 200
    after = next(c for c in store.get_all_cards() if c.id == card_id)
    assert after.repetitions > before.repetitions


def test_wrong_selection_auto_grades_as_failure(client: TestClient) -> None:
    card_id = _mcq_card_id()

    resp = client.post(f"/revision/answer/{card_id}", data={"selected": ["0"]})

    assert resp.status_code == 200
    after = next(c for c in store.get_all_cards() if c.id == card_id)
    assert after.repetitions == 0


def test_unknown_card_falls_back_to_next_card_partial(client: TestClient) -> None:
    resp = client.post("/revision/answer/999", data={"selected": ["0"]})
    assert resp.status_code == 200
