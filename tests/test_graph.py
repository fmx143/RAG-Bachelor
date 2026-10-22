"""Fact graph: triple extraction, storage, deletion and Personalized PageRank ranking."""

from __future__ import annotations

import json

import pytest

from rag_bachelor.core import graph
from rag_bachelor.ingest import index

_VOCAB = ("pivot", "dijkstra", "tri", "chemin")


def fake_embed(texts: list[str]) -> list[list[float]]:
    """Bag-of-vocabulary vectors: texts sharing a word are close, others nearly orthogonal."""
    vecs = [[float(w in t.lower()) for w in _VOCAB] + [0.1] for t in texts]
    return [[x / sum(v * v for v in vec) ** 0.5 for x in vec] for vec in vecs]


class FakeProvider:
    """Answers each chunk text with the JSON registered for it; counts the calls."""

    def __init__(self, replies: dict[str, str]) -> None:
        self.replies, self.calls = replies, 0

    def chat(
        self, messages: list[dict[str, str]], model: str | None = None, json_mode: bool = False
    ) -> str:
        self.calls += 1
        return self.replies[messages[-1]["content"]]


_TRIPLES = {
    "tri rapide": json.dumps({"triples": [["tri rapide", "choisit", "un pivot"]]}),
    "dijkstra": json.dumps({"triples": [["Dijkstra", "calcule", "plus court chemin"]]}),
    "bruit": "ceci n'est pas du JSON",
}


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> FakeProvider:
    fake = FakeProvider(_TRIPLES)
    monkeypatch.setattr(graph, "get_provider", lambda: (fake, "ollama"))
    monkeypatch.setattr(graph, "active_model", lambda name: "m")
    monkeypatch.setattr(graph, "embed", fake_embed)
    return fake


def _index_chunks(**texts: str) -> list[str]:
    """Index chunks c0, c1… of a.pdf (one per keyword argument value) and return their ids."""
    ids = [f"a.pdf__p1__c{i}" for i in range(len(texts))]
    index.get_collection().upsert(
        ids=ids,
        documents=list(texts.values()),
        embeddings=fake_embed(list(texts.values())),  # type: ignore[arg-type]
        metadatas=[{"source": "a.pdf", "page": 1}] * len(ids),  # type: ignore[arg-type]
    )
    return ids


def test_norm_merges_case_accents_and_plurals() -> None:
    assert graph._norm("Complexités") == graph._norm("complexite")
    assert graph._norm("O(n)") == ""  # nothing usable: such facts are dropped


def test_build_graph_stores_facts_and_skips_unparseable_answers(provider: FakeProvider) -> None:
    _index_chunks(a="tri rapide", b="bruit", c="dijkstra")
    progress: list[tuple[int, int]] = []

    n, skipped = graph.build_graph("a.pdf", lambda done, total: progress.append((done, total)))

    assert (n, skipped) == (2, 0)
    assert graph.fact_counts() == {"a.pdf": 2}
    assert provider.calls == 3
    assert progress == [(1, 3), (2, 3), (3, 3)]


def test_rebuilding_replaces_the_facts_instead_of_duplicating(provider: FakeProvider) -> None:
    _index_chunks(a="tri rapide")
    graph.build_graph("a.pdf")
    graph.build_graph("a.pdf")

    assert graph.fact_counts() == {"a.pdf": 1}


def test_delete_source_removes_its_facts(provider: FakeProvider) -> None:
    _index_chunks(a="tri rapide")
    graph.build_graph("a.pdf")
    index.delete_source("a.pdf")

    assert graph.fact_counts() == {}


def test_ranking_puts_the_chunk_tied_to_the_seed_entity_first(provider: FakeProvider) -> None:
    ids = _index_chunks(a="tri rapide", b="dijkstra")
    graph.build_graph("a.pdf")

    assert graph.graph_ranking("Comment choisir le pivot ?", [], {})[0] == ids[0]
    assert graph.graph_ranking("plus court chemin", ["dijkstra"], {})[0] == ids[1]


def test_ranking_is_empty_without_a_graph(provider: FakeProvider) -> None:
    assert graph.graph_ranking("pivot", [], {"a.pdf__p1__c0": 0.9}) == []


def test_graph_is_cached_until_the_facts_change(provider: FakeProvider) -> None:
    _index_chunks(a="tri rapide", b="dijkstra")
    graph.build_graph("a.pdf")
    first = graph._cached_graph()

    assert graph._cached_graph() is first  # nothing changed: same object
    graph.build_graph("a.pdf")
    second = graph._cached_graph()
    assert second is not first  # rebuilt facts invalidate it
    index.delete_source("a.pdf")
    assert graph._cached_graph().number_of_nodes() == 0  # deletion too


class Flaky(FakeProvider):
    """Raises for chunks whose text is registered in *broken*, else behaves like FakeProvider."""

    def __init__(self, replies: dict[str, str], broken: set[str]) -> None:
        super().__init__(replies)
        self.broken = broken

    def chat(
        self, messages: list[dict[str, str]], model: str | None = None, json_mode: bool = False
    ) -> str:
        if messages[-1]["content"] in self.broken:
            raise RuntimeError("429")
        return super().chat(messages, model, json_mode)


def test_failing_chunks_are_retried_then_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = Flaky(_TRIPLES, broken={"dijkstra"})
    monkeypatch.setattr(graph, "get_provider", lambda: (fake, "ollama"))
    monkeypatch.setattr(graph, "active_model", lambda name: "m")
    monkeypatch.setattr(graph, "embed", fake_embed)
    monkeypatch.setattr(graph.time, "sleep", lambda s: None)
    _index_chunks(a="tri rapide", b="dijkstra")

    assert graph.build_graph("a.pdf") == (1, 1)  # one chunk kept, one skipped after retries

    _index_chunks(a="dijkstra")
    with pytest.raises(RuntimeError):  # every chunk failing is an error, not an empty graph
        graph.build_graph("a.pdf")
