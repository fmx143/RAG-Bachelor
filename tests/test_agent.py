"""Agentic RAG loop and keyword extraction, with a scripted LLM and a stubbed retriever."""

from __future__ import annotations

import json

import pytest

from rag_bachelor.config import settings
from rag_bachelor.core import agent, keywords
from rag_bachelor.core.qa import NO_DOC_MSG
from rag_bachelor.core.retriever import SearchResult


class Scripted:
    """LLM that replays canned replies and records every call."""

    def __init__(self, *replies: str) -> None:
        self.replies, self.calls = list(replies), 0

    def chat(
        self, messages: list[dict[str, str]], model: str | None = None, json_mode: bool = False
    ) -> str:
        self.calls += 1
        return self.replies.pop(0)


def _search(query: str, source: str | None = None) -> str:
    return json.dumps({"action": "search", "query": query, "keywords": ["k"], "source": source})


def _answer(text: str) -> str:
    return json.dumps({"action": "answer", "text": text})


def _chunk(page: int) -> SearchResult:
    return SearchResult(text=f"texte {page}", source="a.pdf", page=page, score=0.9)


@pytest.fixture
def setup(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """Install a scripted LLM; retrieve() returns pages 1 then 2 (or what the test sets)."""
    seen: list[dict[str, object]] = []

    def install(*replies: str) -> Scripted:
        llm = Scripted(*replies)
        monkeypatch.setattr(agent, "get_provider", lambda: (llm, "ollama"))
        return llm

    def fake_retrieve(query: str, top_k: int, keywords: list[str], source: str | None):  # type: ignore[no-untyped-def]
        seen.append({"query": query, "source": source, "keywords": keywords})
        return [_chunk(p) for p in sorted({1, len(seen)})]  # page 1 comes back every time

    monkeypatch.setattr(agent, "retrieve", fake_retrieve)
    monkeypatch.setattr(agent, "extract_keywords", lambda q: ["kw"])
    monkeypatch.setattr(agent, "collection_count", lambda: 10)
    monkeypatch.setattr(agent, "list_sources", lambda: ["a.pdf", "b.pdf"])
    return install, seen


def test_question_is_searched_first_as_asked_then_the_agent_adds_searches(setup) -> None:  # type: ignore[no-untyped-def]
    install, seen = setup
    llm = install(_search("dijkstra"), _answer("Voilà [a.pdf, p.1]"))

    answer, chunks, steps = agent.answer_agentic("Compare les deux")

    assert answer == "Voilà [a.pdf, p.1]"
    assert seen[0] == {"query": "Compare les deux", "source": None, "keywords": ["kw"]}
    assert [c.page for c in chunks] == [1, 2]  # page 1 found twice, kept once
    assert [(s.query, s.found) for s in steps] == [("Compare les deux", 1), ("dijkstra", 1)]
    assert llm.calls == 2


def test_agent_can_answer_straight_from_the_first_search(setup) -> None:  # type: ignore[no-untyped-def]
    install, seen = setup
    llm = install(_answer("ok"))

    answer, chunks, steps = agent.answer_agentic("q")

    assert answer == "ok" and len(steps) == 1 and chunks and llm.calls == 1


def test_search_budget_is_capped_then_answer_is_forced(
    setup,
    monkeypatch: pytest.MonkeyPatch,  # type: ignore[no-untyped-def]
) -> None:
    install, seen = setup
    monkeypatch.setattr(settings, "agent_max_steps", 2)  # the first search counts
    llm = install(_search("a"), _answer("réponse forcée"))

    answer, _, steps = agent.answer_agentic("q")

    assert answer == "réponse forcée" and len(steps) == 2 and llm.calls == 2


def test_source_must_be_an_indexed_document(setup) -> None:  # type: ignore[no-untyped-def]
    install, seen = setup
    install(_search("q", "b.pdf"), _search("q", "inconnu.pdf"), _answer("ok"))

    agent.answer_agentic("q")

    assert [s["source"] for s in seen] == [None, "b.pdf", None]


def test_broken_json_action_is_retried_not_returned_as_the_answer(setup) -> None:  # type: ignore[no-untyped-def]
    install, seen = setup
    install('{"action": "search", "query": "q", "keywords": ["a"', _search("q"), _answer("ok"))

    answer, chunks, steps = agent.answer_agentic("q")

    assert answer == "ok" and len(steps) == 2 and chunks


def test_plain_text_reply_is_taken_as_the_answer(setup) -> None:  # type: ignore[no-untyped-def]
    install, seen = setup
    install("Bonjour !")

    assert agent.answer_agentic("salut")[0] == "Bonjour !"


def test_empty_index_short_circuits(setup, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    install, seen = setup
    llm = install()
    monkeypatch.setattr(agent, "collection_count", lambda: 0)

    assert agent.answer_agentic("q") == (NO_DOC_MSG, [], []) and llm.calls == 0


def test_keywords_merge_both_lists_and_tolerate_garbage(monkeypatch: pytest.MonkeyPatch) -> None:
    good = Scripted(
        json.dumps({"bas_niveau": ["PDCA", " Deming "], "haut_niveau": ["qualité", "PDCA"]})
    )
    monkeypatch.setattr(keywords, "get_provider", lambda: (good, "ollama"))
    assert keywords.extract_keywords("q") == ["PDCA", "Deming", "qualité"]

    bad = Scripted("pas du json")
    monkeypatch.setattr(keywords, "get_provider", lambda: (bad, "ollama"))
    assert keywords.extract_keywords("q") == []
