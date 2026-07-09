"""Shared-secret login gate.

Protects every route once the app is reachable from outside localhost (e.g. via
the Cloudflare Tunnel to the NAS). Disabled entirely when APP_PASSWORD is unset,
so local dev stays frictionless.
"""

from __future__ import annotations

import logging
import secrets
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from rag_bachelor.app.web._deps import templates
from rag_bachelor.config import settings

logger = logging.getLogger(__name__)

_ALLOWED_PREFIXES = ("/login", "/static")

router = APIRouter()


def auth_enabled() -> bool:
    return bool(settings.app_password.get_secret_value())


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
        if path.startswith(_ALLOWED_PREFIXES) or request.session.get("auth"):
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
    if auth_enabled() and secrets.compare_digest(password, settings.app_password.get_secret_value()):
        request.session["auth"] = True
        return RedirectResponse(url=safe_next, status_code=303)
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
