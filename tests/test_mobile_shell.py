"""Phone shell (top bar, drawer, bottom tab bar) and the PWA manifest."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from rag_bachelor.app.web import server

_STATIC = Path(server.__file__).parent / "static"


def test_pages_carry_the_phone_bars_and_manifest() -> None:
    with TestClient(server.app) as client:
        page = client.get("/livres").text
    assert 'class="menu-btn" data-menu-toggle aria-controls="sidenav"' in page
    assert '<aside class="side" id="sidenav">' in page
    assert 'class="tabbar"' in page and 'href="/docs" class="add"' in page
    assert '<a href="/livres" class="on">' in page  # current tab highlighted in the bottom bar
    assert '<link rel="manifest" href="/static/manifest.json">' in page
    assert "viewport-fit=cover" in page


def test_manifest_is_installable_and_its_icons_exist() -> None:
    with TestClient(server.app) as client:
        resp = client.get("/static/manifest.json")
        assert resp.status_code == 200 and resp.headers["content-type"].startswith(
            "application/json"
        )
        manifest = json.loads(resp.text)
        assert manifest["display"] == "standalone" and manifest["start_url"] == "/"
        sizes = {i["sizes"] for i in manifest["icons"]}
        assert {"192x192", "512x512"} <= sizes
        for icon in manifest["icons"]:
            assert client.get(icon["src"]).status_code == 200
