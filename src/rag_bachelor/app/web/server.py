"""FastAPI application entry point.

Start with:
    uvicorn rag_bachelor.app.web.server:app --host 0.0.0.0 --port 8090

Or via the installed script:
    rag-web
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from rag_bachelor.app.web import auth
from rag_bachelor.app.web._deps import templates as _templates
from rag_bachelor.app.web.routes import (
    ask,
    bank,
    documentation,
    generate,
    maps,
    progress,
    revision,
    settings,
    sheets,
)
from rag_bachelor.config import settings as cfg

_templates.env.globals["auth_enabled"] = auth.auth_enabled

logger = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).parent / "static"


_STARTUP_ATTEMPTS = 6  # backoff 1+2+4+8+16 s ≈ 30 s for Postgres/Chroma to come up


def _retry(what: str, fn: Callable[[], object]) -> None:
    """Call *fn* with bounded exponential backoff; log and re-raise the last failure."""
    for attempt in range(1, _STARTUP_ATTEMPTS + 1):
        try:
            fn()
            return
        except Exception as exc:
            if attempt == _STARTUP_ATTEMPTS:
                logger.error("%s indisponible après %d tentatives : %s", what, attempt, exc)
                raise
            delay = 2 ** (attempt - 1)
            logger.warning("%s pas prêt (%s), nouvel essai dans %d s", what, exc, delay)
            time.sleep(delay)  # runs in a worker thread (asyncio.to_thread)


def _wait_for_backends() -> None:
    from rag_bachelor.ingest.index import get_collection
    from rag_bachelor.study.store import get_conn

    _retry("PostgreSQL / base d'étude", get_conn)
    _retry("ChromaDB", get_collection)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Pre-warm the embedding model and ChromaDB so the first request doesn't stall."""
    import asyncio

    from rag_bachelor.core.embeddings import get_model

    if not auth.auth_enabled():
        logger.warning(
            "APP_PASSWORD n'est pas défini — l'application tourne sans authentification. "
            "Configure APP_PASSWORD et SESSION_SECRET avant de l'exposer publiquement."
        )

    await asyncio.to_thread(get_model)
    await asyncio.to_thread(_wait_for_backends)
    yield


app = FastAPI(
    title="Assistant Révision — RAG Bachelor",
    lifespan=_lifespan,
    # No Swagger/ReDoc/OpenAPI schema: the HTMX UI doesn't use them, and Swagger loads
    # from a CDN that the CSP below would block anyway.
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

# ── Middleware (order matters: SessionMiddleware must wrap AuthMiddleware so
#    request.session is populated before the auth check runs) ──────────────────
_session_secret = cfg.session_secret.get_secret_value()
auth.check_config()

app.add_middleware(auth.AuthMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=_session_secret or "insecure-dev-only-secret",
    session_cookie="rag_session",
    https_only=cfg.session_cookie_secure,
    same_site="lax",
    max_age=12 * 3600,  # a stolen cookie stops working after half a day (default: 14 days)
)

# Added last ⇒ outermost: also covers AuthMiddleware's redirects.
_SECURITY_HEADERS = {
    # 'unsafe-inline' for styles only (templates use style="" attributes); no inline JS.
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; object-src 'none'; base-uri 'self'; form-action 'self'; "
        "frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
}
if cfg.session_cookie_secure:  # served over https (Cloudflare Tunnel): pin it
    _SECURITY_HEADERS["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"


@app.middleware("http")
async def _security_headers(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    response = await call_next(request)
    response.headers.update(_SECURITY_HEADERS)
    if not request.url.path.startswith("/static"):
        # Pages hold course content and answers: keep them out of browser/proxy caches.
        response.headers["Cache-Control"] = "no-store"
    return response


# ── Static files ───────────────────────────────────────────────────────────────
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

# ── Routers ────────────────────────────────────────────────────────────────────
app.include_router(auth.router)
app.include_router(documentation.router)
app.include_router(ask.router)
app.include_router(revision.router)
app.include_router(generate.router)
app.include_router(bank.router)
app.include_router(sheets.router)
app.include_router(maps.router)
app.include_router(progress.router)
app.include_router(settings.router)


@app.get("/healthz", include_in_schema=False)
def healthz() -> JSONResponse:
    """Liveness of the data tier. Public (tunnel): no detail in the body, causes go to the log."""
    from rag_bachelor.ingest.index import heartbeat
    from rag_bachelor.study.store import get_conn

    try:
        get_conn().execute("SELECT 1")
        heartbeat()
    except Exception:
        logger.exception("healthz : dépendance indisponible")
        return JSONResponse({"status": "unavailable"}, status_code=503)
    return JSONResponse({"status": "ok"})


@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse(url="/docs")  # app's documentation tab, not Swagger UI


# ── CLI entry point ────────────────────────────────────────────────────────────


def main() -> None:
    """Entry point registered as `rag-web` in pyproject.toml."""
    uvicorn.run(
        "rag_bachelor.app.web.server:app",
        # Without a login gate, only this machine may reach it (Docker runs uvicorn directly).
        host="0.0.0.0" if auth.auth_enabled() else "127.0.0.1",
        port=cfg.app_port,
        reload=False,
    )
