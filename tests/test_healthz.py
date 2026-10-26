"""/healthz status codes and store reconnection (no real Postgres/Chroma involved)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from rag_bachelor.app.web import server
from rag_bachelor.ingest import index
from rag_bachelor.study import store


def test_healthz_ok() -> None:
    with TestClient(server.app) as client:
        resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_healthz_503_without_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> None:
        raise RuntimeError("secret-host-detail")

    monkeypatch.setattr(index, "heartbeat", boom)
    with TestClient(server.app) as client:
        resp = client.get("/healthz")
    assert resp.status_code == 503
    assert resp.json() == {"status": "unavailable"}
    assert "secret-host-detail" not in resp.text


def test_startup_retry_gives_up_after_bounded_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server.time, "sleep", lambda _s: None)
    calls: list[int] = []

    def never_ready() -> None:
        calls.append(1)
        raise ConnectionError("down")

    with pytest.raises(ConnectionError):
        server._retry("X", never_ready)
    assert len(calls) == server._STARTUP_ATTEMPTS


def test_dead_postgres_connection_is_reopened(monkeypatch: pytest.MonkeyPatch) -> None:
    class Dead:
        closed = True
        broken = False

        def close(self) -> None:
            pass

    monkeypatch.setattr(store, "_use_postgres", lambda: False)  # reopen path = SQLite here
    monkeypatch.setattr(store, "_usable", lambda c: not isinstance(c, Dead))
    monkeypatch.setattr(store, "_conn", Dead())
    conn = store.get_conn()
    assert not isinstance(conn, Dead)
    conn.execute("SELECT 1")
