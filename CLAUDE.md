# RAG-Bachelor — App-level conventions

This file stacks on top of the global `~/.claude/CLAUDE.md`.

## What this app does

Local-first RAG study assistant for French bachelor PDFs.
- LLM: Ollama (qwen2.5:7b-instruct, default for local dev) or OpenAI (toggle in ⚙️ Paramètres /
  `DEFAULT_LLM_PROVIDER=openai`) — the NAS deployment runs OpenAI-only, no Ollama container
- Embeddings: always local (bge-m3) regardless of LLM provider
- UI: FastAPI + HTMX, 7 tabs (docs, ask, revision, generate, bank, progress, settings)
- Study tracking: SM-2 spaced repetition, persisted to SQLite (local dev) or PostgreSQL
  (`POSTGRES_HOST` set — isolated data-tier container on the NAS)
- Vector index: ChromaDB, embedded `PersistentClient` (local dev) or standalone server via
  `CHROMA_HOST` (isolated data-tier container on the NAS)

## Key paths

| Path | Purpose |
|---|---|
| `src/rag_bachelor/` | All source code |
| `src/rag_bachelor/config.py` | Pydantic-settings (single source of truth for all settings) |
| `src/rag_bachelor/core/llm.py` | `get_provider()` — Ollama or OpenAI, per `DEFAULT_LLM_PROVIDER` / the Settings toggle |
| `src/rag_bachelor/core/qtypes.py` | Question types (free/mcq_single/mcq_multi/tf), difficulty levels, tolerant LLM-JSON parsing |
| `src/rag_bachelor/core/bank.py` | Whole-document question-bank generation, windowed LLM calls, semantic near-duplicate filtering |
| `src/rag_bachelor/ingest/` | PDF → chunks → ChromaDB |
| `src/rag_bachelor/study/store.py` | SM-2 + question-bank persistence — dual backend: SQLite (default) or PostgreSQL (`POSTGRES_HOST` set) |
| `src/rag_bachelor/study/` | SM-2, persistence store, stats |
| `src/rag_bachelor/app/web/` | FastAPI server, route modules, Jinja2 templates, static assets |
| `docker-compose.yml` | NAS deployment: `app` + isolated `postgres`/`chroma` data tier (no Ollama) |
| `data/pdfs/` | User-supplied PDFs (not tracked in git) |
| `data/chroma/` | Local embedded vector index, used only when `CHROMA_HOST` is unset (not tracked in git) |
| `data/app.db` | Local SQLite study DB, used only when `POSTGRES_HOST` is unset (not tracked in git) |

## Run commands

```bash
# Prerequisites — Ollama must be running with the model pulled
ollama serve
ollama pull qwen2.5:7b-instruct

# Local dev (no Docker)
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
uvicorn rag_bachelor.app.web.server:app --host 0.0.0.0 --port 8090
# or: rag-web   (after pip install -e .)

# Docker / NAS (2-tier: app, plus an isolated postgres+chroma data tier)
doppler run -- docker compose up -d --build
# app is served on http://localhost:8090 — front it with a Cloudflare Tunnel
# + Cloudflare Access for remote access; postgres/chroma publish no ports

# Tests
pytest
ruff check src/ tests/
mypy src/
```

## Stack & conventions

- Python 3.13, strict mypy, ruff
- `pydantic-settings` for config — never read env vars directly, always via `settings`
- `chromadb` — `PersistentClient` (local dev) or `HttpClient` (`CHROMA_HOST` set); always pass explicit embeddings (don't use Chroma's default embedding fn)
- FastAPI + Jinja2 + HTMX: each tab is a GET route + partial POST routes; HTMX swaps fragments
- Shared deps: `app/web/_deps.py` exports `templates` (Jinja2) and `sidebar_ctx()` — all routes import from there; `sidebar_ctx()` is synchronous
- All user-visible text in **French**; code comments/docstrings in English
- SM-2: implemented from scratch in `study/srs.py` — no external SRS library
- Blocking DB/file/CPU work in async route handlers must be wrapped in `asyncio.to_thread`
- htmx.min.js is vendored locally in `app/web/static/`; do NOT load from a CDN
- The custom htmx.min.js uses XHR (not fetch) for multipart/form-data to avoid Firefox file-upload bugs

## Port

Default: **8090**. VS Code Dev Container forwards this port. On Windows, avoid ports listed in `.devcontainer/devcontainer.json` → `forwardPorts` as VS Code claims them on the host even when the container isn't running the app.

## Adding a new tab

1. Create `src/rag_bachelor/app/web/routes/<name>.py` with an `APIRouter` and a `GET /<name>` route
2. Add a Jinja2 template `app/web/templates/<name>.html` extending `base.html`
3. Import the router and call `app.include_router(...)` in `app/web/server.py`
4. Add a nav link to `app/web/templates/base.html`

## Chunking strategy

Recursive splitter, separators: `["\n\n", "\n", ". ", " "]`, ~900 chars, ~150 overlap.
Chunks carry `source` (filename) and `page_num` for citations.
