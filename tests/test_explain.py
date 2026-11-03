"""Explanations from the subject's books: book-only retrieval, checked citations, cache."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from rag_bachelor.app.web import server
from rag_bachelor.core import explain
from rag_bachelor.core.retriever import SearchResult
from rag_bachelor.ingest import index
from rag_bachelor.study import store
from rag_bachelor.study.subjects import set_subject_lang

QUIZ = "PMI/Sources/quiz.pdf"
BOOK = "PMI/Livres/PMBOK.pdf"


def _index(*sources: str) -> None:
    index.get_collection().upsert(
        ids=[f"{s}__p1__c0" for s in sources],
        documents=["x"] * len(sources),
        embeddings=[[1.0, 0.0, 0.0]] * len(sources),  # type: ignore[arg-type]
        metadatas=[{"source": s, "page": 1} for s in sources],  # type: ignore[arg-type]
    )


def _hit(page: int) -> SearchResult:
    return SearchResult(text=f"Text p{page}", source=BOOK, page=page, score=1.0)  # type: ignore[call-arg]


def _bank() -> int:
    store.add_bank_questions(
        [store.BankQuestion("What should the PM check?", "Option A is correct.", "moyen", QUIZ,
                            [1], [], qtype="mcq_single", options=["Controls", "Baselines"],
                            correct=[0], origin="imported")]
    )  # fmt: skip
    bank_id = store.list_bank_questions(source=QUIZ)[0].id
    assert bank_id is not None
    return bank_id


def test_resolve_refs_keeps_valid_numbers_and_drops_invented_citations() -> None:
    chunks = [_hit(12), _hit(40)]
    result = explain.resolve_refs("Controls [1]. Also [2, 7]. Fake [PMBOK.pdf, p.99]. [9]", chunks)
    assert result.text == f"Controls [{BOOK}, p.12]. Also [{BOOK}, p.40]. Fake ."
    assert result.refs == [(BOOK, 12), (BOOK, 40)]


def test_explain_searches_only_books_and_writes_in_the_subject_language() -> None:
    _index(QUIZ, BOOK, "Bachelor/Livres/autre.pdf")
    set_subject_lang("PMI", "en")
    provider = MagicMock()
    provider.chat.return_value = "Project controls measure constraints [1]."
    with (
        patch.object(explain, "retrieve", return_value=[_hit(12)]) as retrieve,
        patch.object(explain, "extract_keywords", return_value=["controls"]),
        patch.object(explain, "get_provider", return_value=(provider, "ollama")),
    ):
        result = explain.explain_from_books(
            "What should the PM check?", ["Controls", "B"], [0], QUIZ
        )
    assert retrieve.call_args.kwargs["sources"] == [BOOK]
    assert "Controls" in retrieve.call_args.args[0]
    messages = provider.chat.call_args.args[0]
    assert (
        "ANGLAIS" in messages[0]["content"] and "Bonne réponse : Controls" in messages[1]["content"]
    )
    assert BOOK not in messages[1]["content"]  # the model only sees excerpt numbers
    assert result.refs == [(BOOK, 12)]
    with pytest.raises(explain.NoBooks):
        explain.explain_from_books("Q ?", None, None, "Vide/Sources/q.pdf")


def test_routes_cache_the_bank_explanation_and_serve_revision_cards() -> None:
    bank_id = _bank()
    q = store.get_bank_question(bank_id)
    assert q is not None
    result = explain.BookExplanation(f"Because [{BOOK}, p.12].", [(BOOK, 12)])
    with (
        TestClient(server.app) as client,
        patch("rag_bachelor.app.web.routes.explain.explain_from_books", return_value=result) as run,
    ):
        first = client.post(f"/explain/bank/{bank_id}").text
        again = client.post(f"/explain/bank/{bank_id}").text  # cached: no second LLM call
        assert run.call_count == 1 and "PMBOK p.12" in first and "PMBOK p.12" in again
        client.post(f"/explain/bank/{bank_id}?refresh=true")
        assert run.call_count == 2
        card_id = store.materialize_bank_question(q)
        assert "PMBOK p.12" in client.post(f"/explain/card/{card_id}").text  # via the bank row
        assert run.call_count == 2
    cached = store.get_bank_question(bank_id)
    assert cached is not None and cached.book_explanation == {
        "text": result.text,
        "refs": [[BOOK, 12]],
    }


def test_routes_report_missing_books_and_unconfirmed_questions() -> None:
    bank_id = _bank()
    with TestClient(server.app) as client:
        assert "Aucun livre indexé dans PMI/Livres/" in client.post(f"/explain/bank/{bank_id}").text
        store.add_bank_questions(
            [store.BankQuestion("Pick ?", "", "moyen", QUIZ, [1], [], qtype="mcq_single",
                                options=["X", "Y"], correct=[], origin="imported", to_confirm=True)]
        )  # fmt: skip
        pending = store.list_bank_questions(origin="to_confirm")[0].id
        assert "la bonne réponse de cette question" in client.post(f"/explain/bank/{pending}").text
        assert "introuvable" in client.post("/explain/card/999").text
