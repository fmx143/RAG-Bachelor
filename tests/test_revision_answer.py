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


def _bq(question: str, source: str = "cours.pdf", difficulty: str = "facile") -> store.BankQuestion:
    return store.BankQuestion(
        question=question,
        answer="Une réponse.",
        difficulty=difficulty,
        source=source,
        pages=[1],
        chunk_ids=[f"{source}__p1__c0"],
    )


def test_empty_deck_serves_next_card_from_bank(client: TestClient) -> None:
    store.add_bank_questions([_bq("Bank Q ?")])

    resp = client.get("/revision/next?source=cours.pdf")

    assert resp.status_code == 200
    assert "Bank Q ?" in resp.text
    q = store.list_bank_questions(source="cours.pdf")[0]
    assert q.card_id is not None  # materialized into the deck


def test_filter_mismatch_returns_empty_state(client: TestClient) -> None:
    store.add_bank_questions([_bq("Bank Q ?", source="cours.pdf")])

    resp = client.get("/revision/next?source=autre.pdf")

    assert resp.status_code == 200
    assert "Aucune carte à réviser" in resp.text


def test_grade_pulls_next_bank_question_for_same_filter(client: TestClient) -> None:
    store.add_bank_questions([_bq("Q1 ?", source="cours.pdf"), _bq("Q2 ?", source="cours.pdf")])
    q1 = store.list_bank_questions(source="cours.pdf", order_asc=True)[0]
    card_id = store.materialize_bank_question(q1)

    resp = client.post(
        f"/revision/grade/{card_id}",
        data={"grade": "5", "source": "cours.pdf"},
    )

    assert resp.status_code == 200
    assert "Q2 ?" in resp.text
