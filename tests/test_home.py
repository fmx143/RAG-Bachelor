"""The home page renders on an empty database and answers at the root URL."""

from fastapi.testclient import TestClient

from rag_bachelor.app.web.server import app


def test_home_renders_without_cards() -> None:
    r = TestClient(app).get("/")
    assert r.status_code == 200
    assert "Tes supports" in r.text
