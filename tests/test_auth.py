"""Tests for the shared-secret login gate (src/rag_bachelor/app/web/auth.py)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from starlette.middleware.sessions import SessionMiddleware

from rag_bachelor.app.web import auth
from rag_bachelor.config import settings

_PASSWORD = "correct-horse-battery-staple"
_SESSION_SECRET = "test-only-session-signing-key"


def _build_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(auth.AuthMiddleware)
    app.add_middleware(SessionMiddleware, secret_key=_SESSION_SECRET, session_cookie="rag_session")
    app.include_router(auth.router)

    @app.get("/protected")
    async def protected() -> dict[str, bool]:
        return {"ok": True}

    return app


@pytest.fixture
def auth_enabled_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(settings, "app_password", SecretStr(_PASSWORD))
    auth._failures.clear()
    with TestClient(_build_app()) as client:
        yield client


@pytest.fixture
def auth_disabled_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(settings, "app_password", SecretStr(""))
    with TestClient(_build_app()) as client:
        yield client


# ── auth disabled (empty APP_PASSWORD — local dev default) ─────────────────────


def test_auth_disabled_allows_protected_route(auth_disabled_client: TestClient) -> None:
    resp = auth_disabled_client.get("/protected")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


# ── auth enabled ─────────────────────────────────────────────────────────────


def test_unauthenticated_request_redirects_to_login(auth_enabled_client: TestClient) -> None:
    resp = auth_enabled_client.get("/protected", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/login")


def test_wrong_password_is_rejected(auth_enabled_client: TestClient) -> None:
    resp = auth_enabled_client.post("/login", data={"password": "wrong", "next": "/"})
    assert resp.status_code == 401
    assert "incorrect" in resp.text.lower()


def test_correct_password_grants_access(auth_enabled_client: TestClient) -> None:
    login_resp = auth_enabled_client.post(
        "/login", data={"password": _PASSWORD, "next": "/protected"}, follow_redirects=False
    )
    assert login_resp.status_code == 303
    assert login_resp.headers["location"] == "/protected"

    resp = auth_enabled_client.get("/protected")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_logout_revokes_access(auth_enabled_client: TestClient) -> None:
    auth_enabled_client.post("/login", data={"password": _PASSWORD, "next": "/"})
    auth_enabled_client.get("/logout")

    resp = auth_enabled_client.get("/protected", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/login")


def test_open_redirect_via_next_is_blocked(auth_enabled_client: TestClient) -> None:
    login_resp = auth_enabled_client.post(
        "/login",
        data={"password": _PASSWORD, "next": "https://evil.example.com/phish"},
        follow_redirects=False,
    )
    assert login_resp.status_code == 303
    assert login_resp.headers["location"] == "/"


def test_protocol_relative_next_is_blocked(auth_enabled_client: TestClient) -> None:
    login_resp = auth_enabled_client.post(
        "/login", data={"password": _PASSWORD, "next": "//evil.example.com"}, follow_redirects=False
    )
    assert login_resp.status_code == 303
    assert login_resp.headers["location"] == "/"


def test_login_password_never_echoed_in_error_response(auth_enabled_client: TestClient) -> None:
    resp = auth_enabled_client.post("/login", data={"password": "some-guess", "next": "/"})
    assert "some-guess" not in resp.text


def test_login_locks_out_after_repeated_failures(auth_enabled_client: TestClient) -> None:
    for _ in range(auth._MAX_FAILURES):
        auth_enabled_client.post("/login", data={"password": "wrong", "next": "/"})
    # Even the right password is refused while locked out.
    resp = auth_enabled_client.post("/login", data={"password": _PASSWORD, "next": "/"})
    assert resp.status_code == 429


def test_non_ascii_password_is_rejected_not_crashing(auth_enabled_client: TestClient) -> None:
    resp = auth_enabled_client.post("/login", data={"password": "mötdepasse", "next": "/"})
    assert resp.status_code == 401


def test_check_config_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "app_password", SecretStr(""))
    monkeypatch.setattr(settings, "session_secret", SecretStr(""))
    monkeypatch.setattr(settings, "require_auth", False)
    auth.check_config()  # local dev: open, and that is allowed

    monkeypatch.setattr(settings, "require_auth", True)
    with pytest.raises(RuntimeError, match="REQUIRE_AUTH"):
        auth.check_config()  # e.g. the Doppler token is missing in Docker

    monkeypatch.setattr(settings, "app_password", SecretStr("pw"))
    with pytest.raises(RuntimeError, match="SESSION_SECRET"):
        auth.check_config()  # a password with a guessable cookie-signing key

    monkeypatch.setattr(settings, "session_secret", SecretStr("s"))
    auth.check_config()


def test_app_sends_hardening_headers_and_keeps_static_cacheable() -> None:
    from rag_bachelor.app.web.server import app

    client = TestClient(app)
    page = client.get("/login")
    assert page.headers["Cache-Control"] == "no-store"
    assert page.headers["Cross-Origin-Opener-Policy"] == "same-origin"
    assert "Permissions-Policy" in page.headers
    assert client.get("/static/htmx.min.js").headers.get("Cache-Control") != "no-store"


# ── non-ASCII passwords ──────────────────────────────────────────────────────


def test_non_ascii_wrong_password_is_401_not_500(auth_enabled_client: TestClient) -> None:
    resp = auth_enabled_client.post("/login", data={"password": "é€✓"}, follow_redirects=False)
    assert resp.status_code == 401
    assert "Mot de passe incorrect." in resp.text


def test_non_ascii_correct_password_logs_in(
    auth_enabled_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "app_password", SecretStr("mdp-é€✓"))
    resp = auth_enabled_client.post("/login", data={"password": "mdp-é€✓"}, follow_redirects=False)
    assert resp.status_code == 303


def test_healthz_is_public_but_nothing_else_is(auth_enabled_client: TestClient) -> None:
    assert auth_enabled_client.get("/healthz", follow_redirects=False).status_code != 303
    assert auth_enabled_client.get("/healthzx", follow_redirects=False).status_code == 303
