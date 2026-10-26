"""Shared-secret login gate.

Protects every route once the app is reachable from outside localhost (e.g. via
the Cloudflare Tunnel to the NAS). Disabled entirely when APP_PASSWORD is unset,
so local dev stays frictionless.
"""

from __future__ import annotations

import logging
import secrets
import time
from collections import deque
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from rag_bachelor.app.web._deps import templates
from rag_bachelor.config import settings

logger = logging.getLogger(__name__)

_ALLOWED_PREFIXES = ("/login", "/static")
# Exact match, not a prefix: the Docker healthcheck calls it without a session.
_ALLOWED_PATHS = frozenset({"/healthz"})

router = APIRouter()

# Brute-force guard. Global, not per IP: behind cloudflared every request comes from the
# same local address. ponytail: a flood of bad guesses also locks the owner out for up to
# a minute — acceptable for a single-user app that sits behind Cloudflare Access.
_MAX_FAILURES = 5
_WINDOW_S = 60.0
_failures: deque[float] = deque()


def _locked_out(now: float) -> bool:
    while _failures and now - _failures[0] > _WINDOW_S:
        _failures.popleft()
    return len(_failures) >= _MAX_FAILURES


def auth_enabled() -> bool:
    return bool(settings.app_password.get_secret_value())


def check_config() -> None:
    """Refuse to start with a configuration that would leave the app open or forgeable."""
    if settings.require_auth and not auth_enabled():
        raise RuntimeError(
            "REQUIRE_AUTH est actif mais APP_PASSWORD est vide (token Doppler manquant ?). "
            "Refus de démarrer sans authentification."
        )
    if auth_enabled() and not settings.session_secret.get_secret_value():
        # A fixed fallback here would let anyone reading this public source forge a
        # valid session cookie without ever knowing APP_PASSWORD.
        raise RuntimeError(
            "APP_PASSWORD est défini mais SESSION_SECRET est vide. "
            "Définis SESSION_SECRET (ex: via Doppler) avant de démarrer."
        )


def _safe_next(next_path: str) -> str:
    """Keep the post-login redirect within this app (block open redirects)."""
    if next_path.startswith("/") and not next_path.startswith("//") and "://" not in next_path:
        return next_path
    return "/"


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if not auth_enabled():
            return await call_next(request)
        path = request.url.path
        if (
            path in _ALLOWED_PATHS
            or path.startswith(_ALLOWED_PREFIXES)
            or request.session.get("auth")
        ):
            return await call_next(request)
        return RedirectResponse(url=f"/login?next={_safe_next(path)}", status_code=303)


@router.get("/login", response_class=HTMLResponse, include_in_schema=False)
async def login_page(request: Request, next: str = "/") -> Response:
    return templates.TemplateResponse(
        request, "login.html", {"request": request, "error": None, "next": _safe_next(next)}
    )


@router.post("/login", response_class=HTMLResponse, include_in_schema=False)
async def login_submit(
    request: Request,
    password: Annotated[str, Form()],
    next: Annotated[str, Form()] = "/",
) -> Response:
    safe_next = _safe_next(next)
    now = time.monotonic()
    if _locked_out(now):
        logger.warning("Login bloqué : trop d'échecs récents.")
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "request": request,
                "error": "Trop de tentatives. Réessaie dans une minute.",
                "next": safe_next,
            },
            status_code=429,
        )
    if auth_enabled() and secrets.compare_digest(
        password.encode("utf-8"), settings.app_password.get_secret_value().encode("utf-8")
    ):
        request.session.clear()  # fresh session on privilege change
        request.session["auth"] = True
        return RedirectResponse(url=safe_next, status_code=303)
    _failures.append(now)
    return templates.TemplateResponse(
        request,
        "login.html",
        {"request": request, "error": "Mot de passe incorrect.", "next": safe_next},
        status_code=401,
    )


@router.get("/logout", include_in_schema=False)
async def logout(request: Request) -> Response:
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)
