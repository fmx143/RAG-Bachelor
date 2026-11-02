"""The home page renders on an empty database and answers at the root URL."""

from fastapi.testclient import TestClient

from rag_bachelor.app.web.server import app


def test_home_renders_without_cards() -> None:
    r = TestClient(app).get("/")
    assert r.status_code == 200
    assert "À retravailler" in r.text


def test_home_shows_the_concept_map() -> None:
    html = TestClient(app).get("/").text

    assert "data-carte-savoir" in html and "carte-savoir.js" in html
