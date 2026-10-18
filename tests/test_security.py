"""Markdown sanitising of LLM output and HTTP security headers."""

from __future__ import annotations

from fastapi.testclient import TestClient

from rag_bachelor.app.web._deps import _md_filter
from rag_bachelor.app.web.server import app


def test_md_neutralises_javascript_links() -> None:
    out = str(_md_filter("[clique](javascript:alert(1)) [x](javascript&#58;alert(1))"))
    assert "javascript" not in out
    assert 'href="#"' in out


def test_md_keeps_https_links_and_drops_images() -> None:
    out = str(_md_filter("[doc](https://example.org) ![i](https://evil.example.com/?q=x)"))
    assert 'href="https://example.org"' in out
    assert "<img" not in out
    assert "evil.example.com" not in out


def test_md_escapes_raw_html() -> None:
    assert "<script>" not in str(_md_filter("<script>alert(1)</script>"))


def test_security_headers_and_no_api_docs() -> None:
    client = TestClient(app)  # no `with`: skips the lifespan (no model loading)
    resp = client.get("/static/app.css")
    assert "script-src 'self'" in resp.headers["content-security-policy"]
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["x-content-type-options"] == "nosniff"
    for path in ("/api-docs", "/api-redoc", "/openapi.json"):
        assert client.get(path, follow_redirects=False).status_code in (303, 404)
