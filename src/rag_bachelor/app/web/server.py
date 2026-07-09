"""FastAPI application entry point.

Start with:
    uvicorn rag_bachelor.app.web.server:app --host 0.0.0.0 --port 8090

Or via the installed script:
    rag-web
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from rag_bachelor.app.web import auth
from rag_bachelor.app.web._deps import templates as _templates
from rag_bachelor.app.web.routes import ask, documentation, generate, progress, revision, settings
from rag_bachelor.config import settings as cfg

_templates.env.globals["auth_enabled"] = auth.auth_enabled

logger = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Pre-warm the embedding model and ChromaDB so the first request doesn't stall."""
    import asyncio

    from rag_bachelor.core.embeddings import get_model
    from rag_bachelor.ingest.index import get_collection

    if not auth.auth_enabled():
        logger.warning(
            "APP_PASSWORD n'est pas défini — l'application tourne sans authentification. "
            "Configure APP_PASSWORD et SESSION_SECRET avant de l'exposer publiquement."
        )

    await asyncio.to_thread(get_model)
    await asyncio.to_thread(get_collection)
    yield


app = FastAPI(
    title="Assistant Révision — RAG Bachelor",
    lifespan=_lifespan,
    docs_url="/api-docs",
    redoc_url="/api-redoc",
)

# ── Middleware (order matters: SessionMiddleware must wrap AuthMiddleware so
#    request.session is populated before the auth check runs) ──────────────────
_session_secret = cfg.session_secret.get_secret_value()
if auth.auth_enabled() and not _session_secret:
    # A fixed fallback here would let anyone reading this public source forge a
    # valid session cookie without ever knowing APP_PASSWORD — refuse to start instead.
    raise RuntimeError(
        "APP_PASSWORD est défini mais SESSION_SECRET est vide. "
        "Définis SESSION_SECRET (ex: via Doppler) avant de démarrer."
    )

app.add_middleware(auth.AuthMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=_session_secret or "insecure-dev-only-secret",
    session_cookie="rag_session",
    https_only=cfg.session_cookie_secure,
    same_site="lax",
)

# ── Static files ───────────────────────────────────────────────────────────────
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

# ── Routers ────────────────────────────────────────────────────────────────────
app.include_router(auth.router)
app.include_router(documentation.router)
app.include_router(ask.router)
app.include_router(revision.router)
app.include_router(generate.router)
app.include_router(progress.router)
app.include_router(settings.router)


@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse(url="/docs")  # app's documentation tab, not Swagger UI


# ── CLI entry point ────────────────────────────────────────────────────────────

def main() -> None:
    """Entry point registered as `rag-web` in pyproject.toml."""
    uvicorn.run(
        "rag_bachelor.app.web.server:app",
        host="0.0.0.0",
        port=cfg.app_port,
        reload=False,
    )
