"""AI tutor: verdict → grade, unusable JSON → None, and the /revision/check + /why routes."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from rag_bachelor.app.web.routes import revision
from rag_bachelor.core import tutor
from rag_bachelor.study import store
from rag_bachelor.study.srs import Card


class _FakeProvider:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.prompts: list[str] = []

    def chat(
        self, messages: list[dict[str, str]], model: str | None = None, json_mode: bool = False
    ) -> str:
        self.prompts.append(messages[-1]["content"])
        return self.reply


def _patch(monkeypatch: pytest.MonkeyPatch, reply: str) -> _FakeProvider:
    fake = _FakeProvider(reply)
    monkeypatch.setattr(tutor, "get_provider", lambda: (fake, "fake"))
    monkeypatch.setattr(tutor, "retrieve", lambda *a, **k: [])
    return fake


def _card() -> Card:
    return Card(id=1, question="Q ?", answer="R", topic="T", difficulty="moyen")


@pytest.mark.parametrize(
    ("verdict", "grade"), [("juste", 5), ("partiel", 4), ("faux", 0), ("JUSTE", 5)]
)
def test_verdict_maps_to_grade(monkeypatch: pytest.MonkeyPatch, verdict: str, grade: int) -> None:
    _patch(monkeypatch, f'{{"verdict": "{verdict}", "feedback": "ok", "manque": ""}}')
    result = tutor.grade_answer(_card(), "passage", "ma réponse")
    assert result is not None and result.grade == grade


@pytest.mark.parametrize("reply", ["pas du json", '{"verdict": "peut-être"}', "{}"])
def test_unusable_reply_gives_none(monkeypatch: pytest.MonkeyPatch, reply: str) -> None:
    _patch(monkeypatch, reply)
    assert tutor.grade_answer(_card(), "", "x") is None


def test_answer_is_fenced_and_truncated(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch(monkeypatch, '{"verdict": "faux"}')
    tutor.grade_answer(_card(), "", "z" * 10_000)
    assert fake.prompts[0].count("z") == 2000 and "<<<" in fake.prompts[0]


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(revision.router)
    return TestClient(app)


def _card_id() -> int:
    return store.add_card(question="Q ?", answer="R", topic="T", difficulty="moyen")


def test_check_proposes_grade_without_saving_it(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch(monkeypatch, '{"verdict": "juste", "feedback": "Bravo", "manque": ""}')
    card_id = _card_id()
    resp = client.post(f"/revision/check/{card_id}", data={"user_answer": "R"})
    assert resp.status_code == 200
    assert "Bravo" in resp.text and "suggested" in resp.text
    card = store.get_card(card_id)
    assert card is not None and card.repetitions == 0  # the learner still confirms the grade


def test_check_falls_back_when_model_fails(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*a: object, **k: object) -> None:
        raise RuntimeError("down")

    monkeypatch.setattr(tutor, "get_provider", boom)
    resp = client.post(f"/revision/check/{_card_id()}", data={"user_answer": "R"})
    assert resp.status_code == 200
    assert "n'a pas abouti" in resp.text and "suggested" not in resp.text


def test_why_explains_and_reports_model_failure(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch(monkeypatch, "Parce que **R** [p.3].")
    card_id = _card_id()
    assert "Pourquoi" in client.post(f"/revision/why/{card_id}").text

    def boom(*a: object, **k: object) -> None:
        raise RuntimeError("down")

    monkeypatch.setattr(tutor, "get_provider", boom)
    assert "Impossible de joindre" in client.post(f"/revision/why/{card_id}").text
