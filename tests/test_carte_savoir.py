"""Carte du savoir: graph → JSON contract, and the routes that serve it."""

from __future__ import annotations

from datetime import date

import networkx as nx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from rag_bachelor.app.web.server import app
from rag_bachelor.config import settings
from rag_bachelor.core import carte_savoir
from rag_bachelor.core.carte_savoir import construire_carte, graphe_annote
from rag_bachelor.ingest.index import get_facts_collection
from rag_bachelor.study import store
from rag_bachelor.study.store import Chapter


def _graph() -> nx.Graph:
    g: nx.Graph = nx.Graph()
    g.add_node("a", type="concept", nom="A", module="m1", module_nom="Module 1", maitrise=0.5)
    g.add_node(
        "b",
        type="concept",
        module="m2",
        a_revoir=True,
        prochaine_revision=date(2026, 11, 15),
    )
    g.add_node("c:chunk", type="chunk")  # not a concept
    g.add_node("sans-type")
    g.add_edges_from([("a", "b"), ("a", "c:chunk"), ("a", "sans-type"), ("a", "a")])
    return g


def test_construire_carte_keeps_only_concepts_and_known_links() -> None:
    carte = construire_carte(_graph(), "Titre")

    assert [c.id for c in carte.concepts] == ["a", "b"]
    assert [(m.id, m.nom) for m in carte.modules] == [("m1", "Module 1"), ("m2", "m2")]
    assert [(link.source, link.target) for link in carte.liens] == [("a", "b")]


def test_construire_carte_null_mastery_and_iso_dates() -> None:
    data = construire_carte(_graph(), "Titre").model_dump(mode="json")
    b = next(c for c in data["concepts"] if c["id"] == "b")

    assert b["maitrise"] is None and b["a_revoir"] is True
    assert b["prochaine_revision"] == "2026-11-15"
    assert next(c for c in data["concepts"] if c["id"] == "a")["prochaine_revision"] is None


@pytest.fixture(autouse=True)
def _one_chapter(monkeypatch: pytest.MonkeyPatch) -> None:
    """No PDF in tests: every book is a single chapter unless a test says otherwise."""
    monkeypatch.setattr(carte_savoir, "chapters_for", lambda source: [Chapter("Tout le livre", 1, 99)])


def _fact(i: int, subj: str, obj: str, source: str, chunk: str) -> dict[str, object]:
    return {
        "ids": [f"f{i}"],
        "embeddings": [[1.0, 0.0]],
        "documents": [f"{subj.title()} | est | {obj}"],
        "metadatas": [{"source": source, "chunk_id": chunk, "subject": subj, "object": obj}],
    }


def test_graphe_annote_scopes_subject_and_links_cards(monkeypatch: pytest.MonkeyPatch) -> None:
    facts = get_facts_collection()
    facts.upsert(**_fact(1, "bilan", "passif", "Bachelor/Livres/a.pdf", "c1"))  # type: ignore[arg-type]
    facts.upsert(**_fact(2, "actif", "stock", "Bachelor/Livres/a.pdf", "c2"))  # type: ignore[arg-type]
    facts.upsert(**_fact(3, "autre", "chose", "Autre/b.pdf", "c3"))  # type: ignore[arg-type]
    card_id = store.add_card("Q ?", "R.", topic="Bachelor/Livres/a.pdf", difficulty="facile")
    monkeypatch.setattr(carte_savoir, "bank_chunk_cards", lambda: {"c1": {card_id}})

    g = graphe_annote("Bachelor", str.upper, today=date.today())

    assert sorted(g.nodes) == ["actif", "bilan", "passif", "stock"]  # the other subject is out
    assert g.nodes["bilan"]["nom"] == "Bilan" and g.nodes["bilan"]["module_nom"] == "BACHELOR/LIVRES/A.PDF"
    assert g.nodes["bilan"]["maitrise"] is None  # the card was never reviewed
    assert g.nodes["bilan"]["prochaine_revision"] == date.today() and g.nodes["bilan"]["a_revoir"]
    assert g.nodes["passif"]["prochaine_revision"] == date.today()  # same fact, same chunk
    assert g.nodes["actif"]["prochaine_revision"] is None  # no card for chunk c2


def test_graphe_annote_caps_concepts_by_degree() -> None:
    facts = get_facts_collection()
    for i, obj in enumerate(["x", "y", "z"]):
        facts.upsert(**_fact(i, "hub", obj, "Bachelor/Livres/a.pdf", f"c{i}"))  # type: ignore[arg-type]

    g = graphe_annote("Bachelor", str, max_concepts=2)

    assert "hub" in g.nodes and g.number_of_nodes() == 2


def test_api_returns_the_contract_on_an_empty_index() -> None:
    r = TestClient(app).get("/api/carte-savoir")

    assert r.status_code == 200
    assert set(r.json()) == {"titre", "modules", "concepts", "liens"}


def test_page_renders_the_map_markup() -> None:
    r = TestClient(app).get("/carte-savoir")

    assert r.status_code == 200
    assert "data-carte-savoir" in r.text and "carte-savoir.js" in r.text


def test_routes_redirect_to_login_without_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "app_password", SecretStr("pw"))
    client = TestClient(app, follow_redirects=False)

    for path in ("/api/carte-savoir", "/carte-savoir"):
        r = client.get(path)
        assert r.status_code == 303 and r.headers["location"].startswith("/login")


def test_reviser_redirects_to_the_concept_document() -> None:
    get_facts_collection().upsert(**_fact(1, "bilan", "actif", "Bachelor/Livres/a b.pdf", "c1"))  # type: ignore[arg-type]
    client = TestClient(app, follow_redirects=False)

    r = client.get("/carte-savoir/reviser", params={"concept": "bilan"})

    assert r.status_code == 303 and r.headers["location"] == "/revision?source=Bachelor/Livres/a%20b.pdf"
    assert client.get("/carte-savoir/reviser").headers["location"] == "/revision"


def test_graphe_annote_cap_spreads_over_documents() -> None:
    facts = get_facts_collection()
    for i, obj in enumerate(["x", "y", "z"]):
        facts.upsert(**_fact(i, "hub", obj, "Bachelor/Livres/a.pdf", f"c{i}"))  # type: ignore[arg-type]
    facts.upsert(**_fact(9, "solo", "duo", "Bachelor/Livres/b.pdf", "c9"))  # type: ignore[arg-type]

    g = graphe_annote("Bachelor", str, max_concepts=2)

    assert {g.nodes[n]["module"] for n in g.nodes} == {"Bachelor/Livres/a.pdf", "Bachelor/Livres/b.pdf"}


def test_graphe_annote_books_only_one_module_per_chapter(monkeypatch: pytest.MonkeyPatch) -> None:
    book = "Bachelor/Livres/a.pdf"
    monkeypatch.setattr(
        carte_savoir, "chapters_for", lambda source: [Chapter("Un", 1, 9), Chapter("Deux", 10, 20)]
    )
    facts = get_facts_collection()
    facts.upsert(**_fact(1, "bilan", "passif", book, f"{book}__p3__c0"))  # type: ignore[arg-type]
    facts.upsert(**_fact(2, "actif", "stock", book, f"{book}__p12__c0"))  # type: ignore[arg-type]
    facts.upsert(**_fact(3, "note", "texte", "Bachelor/Sources/n.pdf", "c3"))  # type: ignore[arg-type]

    g = graphe_annote("Bachelor", str)

    assert "note" not in g.nodes  # a source is not a book
    assert g.nodes["bilan"]["module"] == f"{book}#Un"
    assert g.nodes["actif"]["module_nom"] == f"{book} · Deux"


def test_reviser_ignores_sources() -> None:
    facts = get_facts_collection()
    facts.upsert(**_fact(1, "bilan", "actif", "Bachelor/Sources/s.pdf", "c1"))  # type: ignore[arg-type]
    facts.upsert(**_fact(2, "bilan", "stock", "Bachelor/Livres/l.pdf", "c2"))  # type: ignore[arg-type]

    r = TestClient(app, follow_redirects=False).get("/carte-savoir/reviser", params={"concept": "bilan"})

    assert r.headers["location"] == "/revision?source=Bachelor/Livres/l.pdf"
