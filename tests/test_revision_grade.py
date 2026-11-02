"""POST /revision/grade/{card_id}: 'Difficile' (grade 3) must not reset the interval."""

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


def _card_id() -> int:
    return store.add_card(question="Q ?", answer="R", topic="T", difficulty="moyen")


def _grade(client: TestClient, card_id: int, grade: int) -> None:
    resp = client.post(f"/revision/grade/{card_id}", data={"grade": str(grade)})
    assert resp.status_code == 200


def test_difficile_grade_3_is_a_pass_not_a_reset(client: TestClient) -> None:
    card_id = _card_id()
    _grade(client, card_id, 3)

    card = store.get_card(card_id)
    assert card is not None
    assert (card.repetitions, card.interval) == (1, 1)
    assert card.stability == pytest.approx(1.18385)  # FSRS-5 S0 for Hard


def test_second_difficile_success_keeps_the_streak(client: TestClient) -> None:
    card_id = _card_id()
    _grade(client, card_id, 3)
    _grade(client, card_id, 3)

    card = store.get_card(card_id)
    assert card is not None
    assert card.repetitions == 2


def test_legacy_grade_2_is_treated_as_unknown_and_resets(client: TestClient) -> None:
    card_id = _card_id()
    _grade(client, card_id, 5)
    _grade(client, card_id, 2)

    card = store.get_card(card_id)
    assert card is not None
    assert (card.repetitions, card.interval) == (0, 1)
