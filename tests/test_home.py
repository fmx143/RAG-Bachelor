"""The home page renders on an empty database and answers at the root URL."""

from fastapi.testclient import TestClient

from rag_bachelor.app.web.server import app


def test_home_renders_without_cards() -> None:
    r = TestClient(app).get("/")
    assert r.status_code == 200
    assert "À retravailler" in r.text


def test_home_knowledge_map_draws_nodes_and_cross_document_links(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from rag_bachelor.app.web.routes import home
    from rag_bachelor.study import store

    store.add_card("Q ?", "R.", topic="Bachelor/a.pdf", difficulty="facile")
    store.add_card("Q2 ?", "R2.", topic="Bachelor/b.pdf", difficulty="facile")
    monkeypatch.setattr(home, "fact_counts", lambda: {"Bachelor/a.pdf": 3})
    monkeypatch.setattr(home, "shared_links", lambda: {("Bachelor/a.pdf", "Bachelor/b.pdf"): 2})

    html = TestClient(app).get("/").text

    assert html.count('class="gnode"') == 2 and html.count('class="xlink"') == 1
    assert "3 faits extraits · 1 lien entre documents" in html
