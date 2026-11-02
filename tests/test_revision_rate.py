"""POST /revision/rate and /revision/delete: relevance toggle, delete card + bank question."""

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


def test_rate_sets_then_clears_on_repeat(client: TestClient) -> None:
    cid = _card_id()
    assert client.post(f"/revision/rate/{cid}", data={"value": "-1"}).status_code == 200
    assert store.get_card(cid).relevance == -1  # type: ignore[union-attr]
    client.post(f"/revision/rate/{cid}", data={"value": "-1"})
    assert store.get_card(cid).relevance is None  # type: ignore[union-attr]


def test_rate_rejects_unknown_value(client: TestClient) -> None:
    assert client.post(f"/revision/rate/{_card_id()}", data={"value": "5"}).status_code == 404


def test_delete_removes_card_and_its_bank_question(client: TestClient) -> None:
    cid = _card_id()
    conn = store.get_conn()
    conn.execute(
        "INSERT INTO question_bank (question, answer, difficulty, source, pages, chunk_ids, card_id)"
        " VALUES ('Q ?', 'R', 'moyen', 'T', '[1]', '[]', ?)",
        (cid,),
    )
    conn.commit()
    assert client.post(f"/revision/delete/{cid}").status_code == 200
    assert store.get_card(cid) is None
    assert (
        conn.execute("SELECT COUNT(*) FROM question_bank WHERE card_id = ?", (cid,)).fetchone()[0]
        == 0
    )
