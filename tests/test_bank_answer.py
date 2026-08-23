"""Inline bank self-test: auto-grading, auto-add-to-révision on failure, and filters."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from rag_bachelor.app.web.routes import bank
from rag_bachelor.study import store


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(bank.router)
    return TestClient(app)


def _mcq(question: str = "2 + 2 = ?", source: str = "cours.pdf") -> store.BankQuestion:
    return store.BankQuestion(
        question=question,
        answer="4 est la bonne réponse.",
        difficulty="facile",
        source=source,
        pages=[1],
        chunk_ids=[f"{source}__p1__c0"],
        qtype="mcq_single",
        options=["3", "4", "5"],
        correct=[1],
    )


def _free(question: str = "Qu'est-ce qu'un algorithme ?", source: str = "cours.pdf") -> store.BankQuestion:
    return store.BankQuestion(
        question=question,
        answer="Une suite finie d'instructions.",
        difficulty="facile",
        source=source,
        pages=[2],
        chunk_ids=[f"{source}__p2__c0"],
    )


def test_wrong_mcq_answer_auto_adds_to_revision(client: TestClient) -> None:
    store.add_bank_questions([_mcq()])
    q = store.list_bank_questions()[0]
    assert q.id is not None and q.card_id is None

    resp = client.post(f"/bank/{q.id}/answer", data={"selected": ["0"]})  # wrong option

    assert resp.status_code == 200
    assert "Mauvaise réponse" in resp.text
    assert "Ajoutée à la révision" in resp.text

    refreshed = store.get_bank_question(q.id)
    assert refreshed is not None
    assert refreshed.last_result == 0
    assert refreshed.card_id is not None

    cards = store.get_all_cards()
    assert len(cards) == 1
    assert cards[0].question == q.question


def test_correct_mcq_answer_records_result_without_adding_card(client: TestClient) -> None:
    store.add_bank_questions([_mcq()])
    q = store.list_bank_questions()[0]

    resp = client.post(f"/bank/{q.id}/answer", data={"selected": ["1"]})  # correct option

    assert resp.status_code == 200
    assert "Bonne réponse" in resp.text
    assert "Ajoutée à la révision" not in resp.text

    refreshed = store.get_bank_question(q.id)
    assert refreshed is not None
    assert refreshed.last_result == 1
    assert refreshed.card_id is None
    assert store.get_all_cards() == []


def test_free_text_self_grade_incorrect_adds_to_revision(client: TestClient) -> None:
    store.add_bank_questions([_free()])
    q = store.list_bank_questions()[0]

    resp = client.post(f"/bank/{q.id}/answer", data={"self_grade": "incorrect"})

    assert resp.status_code == 200
    refreshed = store.get_bank_question(q.id)
    assert refreshed is not None
    assert refreshed.last_result == 0
    assert refreshed.card_id is not None


def test_free_text_self_grade_correct_does_not_add(client: TestClient) -> None:
    store.add_bank_questions([_free()])
    q = store.list_bank_questions()[0]

    resp = client.post(f"/bank/{q.id}/answer", data={"self_grade": "correct"})

    assert resp.status_code == 200
    refreshed = store.get_bank_question(q.id)
    assert refreshed is not None
    assert refreshed.last_result == 1
    assert refreshed.card_id is None


def test_already_linked_question_is_not_re_added_on_a_second_failure(client: TestClient) -> None:
    store.add_bank_questions([_mcq()])
    q = store.list_bank_questions()[0]
    assert q.id is not None
    client.post(f"/bank/{q.id}/answer", data={"selected": ["0"]})  # first failure: adds card
    client.post(f"/bank/{q.id}/answer", data={"selected": ["2"]})  # second failure: no new card

    assert len(store.get_all_cards()) == 1


def test_answer_unknown_question_returns_warning(client: TestClient) -> None:
    resp = client.post("/bank/999/answer", data={"selected": ["0"]})
    assert resp.status_code == 200
    assert "introuvable" in resp.text


def test_list_filters_by_qtype_result_and_deck(client: TestClient) -> None:
    store.add_bank_questions([_mcq("Q-mcq ?"), _free("Q-free ?")])
    mcq = next(q for q in store.list_bank_questions() if q.qtype == "mcq_single")
    assert mcq.id is not None
    client.post(f"/bank/{mcq.id}/answer", data={"selected": ["0"]})  # wrong -> linked + incorrect

    resp = client.get("/bank/list", params={"qtype": "mcq_single", "result": "incorrect", "deck": "in"})
    assert resp.status_code == 200
    assert "Q-mcq" in resp.text
    assert "Q-free" not in resp.text

    # Filtering by "untried" should now exclude the mcq (it was attempted) but keep the free one.
    resp2 = client.get("/bank/list", params={"result": "untried"})
    assert "Q-free" in resp2.text
    assert "Q-mcq" not in resp2.text


def test_add_all_links_every_unlinked_matching_question(client: TestClient) -> None:
    """Regression: linking a question must not make add-all skip later matches
    under the deck="out" filter, which shrinks as each match gets linked."""
    store.add_bank_questions([_mcq(f"Q{i} ?") for i in range(5)])
    resp = client.post("/bank/add-all", data={"deck": "out"})

    assert resp.status_code == 200
    assert "5 carte(s) ajoutée(s)" in resp.text
    assert all(q.card_id is not None for q in store.list_bank_questions())
    assert len(store.get_all_cards()) == 5
