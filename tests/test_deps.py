"""app/web/_deps.py: asset_url() cache-busting query string."""

from __future__ import annotations

from rag_bachelor.app.web._deps import asset_url


def test_asset_url_appends_mtime_for_existing_file() -> None:
    url = asset_url("app.css")
    assert url.startswith("/static/app.css?v=")
    assert url.rsplit("=", 1)[1].isdigit()


def test_asset_url_falls_back_without_version_for_missing_file() -> None:
    assert asset_url("does-not-exist.css") == "/static/does-not-exist.css"
