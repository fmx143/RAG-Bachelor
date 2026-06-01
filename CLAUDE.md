# RAG-Bachelor — App-level conventions

This file stacks on top of the global `~/.claude/CLAUDE.md`.

## What this app does

Local-first RAG study assistant for French bachelor PDFs.
- Online: OpenAI (gpt-4o-mini / gpt-4o) for LLM; always local embeddings (bge-m3)
- Offline: Ollama (qwen2.5:7b-instruct) auto-selected when no internet
- UI: Streamlit, 6 tabs (docs, ask, revision, generate, progress, settings)
- Study tracking: SM-2 spaced repetition, SQLite

## Key paths

| Path | Purpose |
|---|---|
| `src/rag_bachelor/` | All source code |
| `src/rag_bachelor/config.py` | Pydantic-settings (single source of truth for all settings) |
| `src/rag_bachelor/core/llm.py` | LLM provider abstraction + connectivity cache |
| `src/rag_bachelor/ingest/` | PDF → chunks → ChromaDB |
| `src/rag_bachelor/study/` | SM-2, SQLite store, stats |
| `src/rag_bachelor/app/` | Streamlit entry + tab modules |
| `data/pdfs/` | User-supplied PDFs (not tracked in git) |
| `data/chroma/` | Persistent vector index (not tracked in git) |
| `data/app.db` | SQLite study DB (not tracked in git) |

## Run commands

```bash
# Local dev (no Docker)
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # fill in OPENAI_API_KEY
streamlit run src/rag_bachelor/app/main.py

# Docker (recommended for portability)
docker compose up --build         # app only
docker compose --profile local-llm up --build  # with bundled Ollama

# Tests
pytest
ruff check src/ tests/
mypy src/
```

## Stack & conventions

- Python 3.13, strict mypy, ruff
- `pydantic-settings` for config — never read env vars directly, always via `settings`
- `chromadb` PersistentClient — always pass explicit embeddings (don't use Chroma's default embedding fn)
- Streamlit: one function `render()` per tab module; no global state except `st.session_state`
- All user-visible text in **French**; code comments/docstrings in English
- SM-2: implemented from scratch in `study/srs.py` — no external SRS library
- Connectivity check is cached with a TTL; use `_ConnectivityCache` in `llm.py`, never ping externally in other modules

## Adding a new tab

1. Create `src/rag_bachelor/app/tabs/<name>.py` with a `render() -> None` function
2. Import and wire it into `app/main.py`

## Chunking strategy

Recursive splitter, separators: `["\n\n", "\n", ". ", " "]`, ~900 chars, ~150 overlap.
Chunks carry `source` (filename) and `page_num` for citations.
