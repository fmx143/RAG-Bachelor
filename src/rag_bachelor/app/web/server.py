"""FastAPI application entry point.

Start with:
    uvicorn rag_bachelor.app.web.server:app --host 0.0.0.0 --port 8090

Or via the installed script:
    rag-web
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from rag_bachelor.app.web.routes import ask, documentation, generate, progress, revision, settings

_STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Pre-warm the embedding model and ChromaDB so the first request doesn't stall."""
    import asyncio
    from rag_bachelor.core.embeddings import get_model
    from rag_bachelor.ingest.index import get_collection

    await asyncio.to_thread(get_model)
    await asyncio.to_thread(get_collection)
    yield


app = FastAPI(
    title="Assistant Révision — RAG Bachelor",
    lifespan=_lifespan,
    docs_url="/api-docs",
    redoc_url="/api-redoc",
)

# ── Static files ───────────────────────────────────────────────────────────────
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

# ── Routers ────────────────────────────────────────────────────────────────────
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
        port=settings.app_port,
        reload=False,
    )
