# 🎓 RAG-Bachelor — Assistant de révision local

A local-first RAG study assistant for French bachelor PDF documents.  
Ask questions about your courses, generate easy/medium/hard revision questions, and track your progress with spaced repetition — **fully offline-capable** via Ollama.

---

## Features

| | |
|---|---|
| 📚 **Document management** | Upload PDFs, index them into a local vector store, re-index or remove them |
| ❓ **Q&A with citations** | Ask anything in French, get a sourced answer with file name + page number |
| 🔄 **Spaced repetition** | SM-2 algorithm (Anki-style) — review due cards, self-grade, auto-reschedule |
| 🎯 **Question generation** | LLM-generated easy / medium / hard questions per topic, add them to your deck |
| 📊 **Progress tracking** | Per-topic mastery bars, weak vs strong subject overview |
| ⚙️ **Local-first** | Ollama for LLM by default (fully offline, no API key needed); OpenAI available as an optional manual toggle in Settings — bge-m3 embeddings are always local |
| 🔒 **Secrets via Doppler** | No API keys or passwords ever live in a `.env` file or the image — see [Configuration & sécurité](#configuration--sécurité-doppler) |

---

## Requirements

| Tool | Version | Purpose |
|---|---|---|
| Python | ≥ 3.13 | Local dev |
| Docker Desktop | any recent | VS Code Dev Container |
| VS Code + Dev Containers extension | any | Container-based dev on Mac / WSL |
| Ollama | latest | Local LLM (required) |

> **Mac users:** Install Ollama natively from [ollama.com](https://ollama.com/download) for GPU acceleration.  
> Docker on Mac cannot pass the GPU through, so Ollama must run on the host.

---

## Option A — VS Code Dev Container (recommended for Mac / WSL)

This is the zero-setup path. VS Code builds the Docker image from the `Dockerfile`
and mounts your local `data/` folder so everything persists across rebuilds.

### 1. Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) running
- VS Code with the **Dev Containers** extension (`ms-vscode-remote.remote-containers`)

### 2. Configure

No `.env` file is needed. If you use the Ollama defaults, no configuration at all is
required. If you want the optional OpenAI provider or the login gate, use Doppler —
see [Configuration & sécurité (Doppler)](#configuration--sécurité-doppler) below.

### 3. Open in container

- Open the `RAG-Bachelor` folder in VS Code.
- A notification pops up: **"Reopen in Container"** — click it.
- Alternatively: `Ctrl+Shift+P` → **Dev Containers: Reopen in Container**.

VS Code builds the image (first time ~5 min, mostly downloading PyTorch) and installs
Python/Ruff extensions. The app does **not** auto-start — start it yourself once the
container is ready (see below).

### 4. Start and open the app

Open a terminal inside the container (`Ctrl+` `` ` ``) and run:

```bash
uvicorn rag_bachelor.app.web.server:app --host 0.0.0.0 --port 8090
```

Then open **http://localhost:8090** in your browser.  
Look in the VS Code **Ports** panel if the URL doesn't open automatically.

### Rebuilding after dependency changes

```
Ctrl+Shift+P → Dev Containers: Rebuild Container
```

---

## Option B — Local Python (no Docker)

```bash
# 1. Create a virtual environment
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# 2. Install dependencies  (~3 GB first time — includes PyTorch)
pip install -e ".[dev]"

# 3. Launch (Ollama defaults — no secrets needed)
uvicorn rag_bachelor.app.web.server:app --host 0.0.0.0 --port 8090
# or: rag-web   (after pip install -e .)

# 4. Or launch through Doppler, once you want OpenAI/auth (see next section)
doppler run -- rag-web
```

Open **http://localhost:8090**.

---

## Configuration & sécurité (Doppler)

This app takes **no `.env` file** — there is nothing to copy, and nothing sensitive
ever lives on disk. Settings are read from real process environment variables
(`pydantic-settings` in `config.py`), and [Doppler](https://doppler.com) is how those
variables get there, both locally and on the NAS. With nothing configured, the app
runs fully offline against Ollama with no login gate — Doppler is only needed once
you want the optional OpenAI provider and/or the login gate.

### Secret names

| Variable | Required for | Notes |
|---|---|---|
| `OLLAMA_HOST` | Ollama (default) | e.g. `http://host.docker.internal:11434` on Mac + Dev Container, `http://localhost:11434` locally |
| `OLLAMA_MODEL` | Ollama (default) | e.g. `qwen2.5:7b-instruct` |
| `OPENAI_API_KEY` | Optional OpenAI provider | Only read at request time; never logged, templated, or echoed back on error |
| `OPENAI_MODEL` | Optional OpenAI provider | e.g. `gpt-4o-mini` |
| `DEFAULT_LLM_PROVIDER` | Optional | `ollama` (default) or `openai` — startup default; toggle in ⚙️ Paramètres overrides it afterwards |
| `APP_PASSWORD` | Login gate | Empty/unset ⇒ gate disabled (fine for local dev, **required** before exposing the app publicly) |
| `SESSION_SECRET` | Login gate | Required whenever `APP_PASSWORD` is set — signs the session cookie; app refuses to start otherwise |
| `SESSION_COOKIE_SECURE` | Login gate over HTTPS | Set `true` once served through the Cloudflare Tunnel (TLS terminates there) |

### Local dev

```bash
doppler login                    # once per machine
doppler setup                    # links this directory to a Doppler project/config
doppler secrets set OPENAI_API_KEY APP_PASSWORD SESSION_SECRET   # only the ones you need

doppler run -- rag-web           # or: doppler run -- uvicorn rag_bachelor.app.web.server:app --port 8090
```

### NAS / Docker

The Doppler CLI is baked into the `Dockerfile`; the container's `ENTRYPOINT`
(`docker/entrypoint.sh`) runs `doppler run -- uvicorn ...`. The **only** secret that
needs to reach the NAS is a scoped, revocable **Doppler Service Token** — the real
`OPENAI_API_KEY` / `APP_PASSWORD` never touch the NAS filesystem or the image.

```bash
# Create a service token scoped to this Doppler project/config, then on the NAS:
docker run -e DOPPLER_TOKEN=dp.st.xxxxx ...
# or, preferably, mount it as a file (not visible via `docker inspect`/`ps`):
docker run -e DOPPLER_TOKEN_FILE=/run/secrets/doppler_token -v /path/to/token:/run/secrets/doppler_token:ro ...
```

If the NAS is ever compromised, **revoke the Doppler token** from the Doppler
dashboard — no key rotation is needed anywhere else.

If `DOPPLER_TOKEN`/`DOPPLER_TOKEN_FILE` isn't set, the entrypoint starts the app
directly without Doppler (Ollama-only, no login gate) — useful for a plain local
`docker compose up` with no secrets involved.

### Defense in depth

Put [Cloudflare Access](https://developers.cloudflare.com/cloudflare-one/policies/access/)
in front of the tunnel as a second layer on top of the app's own login gate.

> **Embeddings are always local** (BAAI/bge-m3) — no key needed for that part regardless.

---

## Setting up Ollama (offline / local LLM)

### macOS

```bash
# Install (or download from https://ollama.com/download)
brew install ollama

# Start the server
ollama serve

# Pull the model the app uses (~4.7 GB download)
ollama pull qwen2.5:7b-instruct
```

Set `OLLAMA_HOST=http://host.docker.internal:11434` when running inside the Dev Container,
or `http://localhost:11434` when running locally (via Doppler, or just export it directly).

### Linux / WSL

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen2.5:7b-instruct  # ~4.7 GB download
# OLLAMA_HOST=http://localhost:11434
```

---

## Using the app

### 1 — Add and index your PDFs

**Tab: 📚 Documentation**

1. Drag and drop your PDFs onto the **upload area** — they are saved to `data/pdfs/`.
2. Click **🔄 (Re)indexer tous les PDFs** to index everything at once,  
   or click **Indexer** next to a single file.
3. The chunk count updates after indexing. A warning appears for blank/image-only pages.
4. To remove a document, click 🗑️ — it is deleted from disk **and** from the index.

> **Re-indexing:** Replace a PDF and click **Indexer** — old chunks are removed automatically.

---

### 2 — Ask a question

**Tab: ❓ Poser une question**

1. Type your question in French.
2. Adjust **Sources utilisées** (3–10) to control how many passages are retrieved.
3. Click **🔍 Obtenir une réponse**.
4. The answer cites sources as `[fichier.pdf, p.X]`. Expand **📖 Sources utilisées** to see the raw passages.

---

### 3 — Generate study questions

**Tab: 🎯 Générer des questions**

1. Select a **document** from the dropdown or type a **free topic** (e.g. *Complexité algorithmique*).
2. Choose a difficulty: 🟢 **Facile** · 🟡 **Moyen** · 🔴 **Difficile**.
3. Click **✨ Générer des questions** — 3 questions grounded in your course content are produced.
4. Optionally write a model answer, then click **➕ Ajouter à la révision** to add the card to your deck.

---

### 4 — Revise with spaced repetition

**Tab: 🔄 Révision**

Cards due today are shown one at a time. Click **👁️ Afficher la réponse** when ready, then grade yourself:

| Button | Effect |
|---|---|
| 😰 Raté / 🤔 Difficile | Resets the card — back to 1 day |
| 😊 Bien / 🌟 Parfait | Advances the card — interval grows via SM-2 |

---

### 5 — Track your progress

**Tab: 📊 Progrès**

- **Summary metrics:** total cards, due today, average ease factor.
- **Per-topic mastery bars** (weakest first):
  - 🔴 < 40% — needs work · 🟡 40–70% — progressing · 🟢 > 70% — mastered
- **À renforcer / Points forts** columns for a quick overview.

---

### 6 — Settings

**Tab: ⚙️ Paramètres**

- View the active LLM provider (Ollama/OpenAI), its model, and whether an OpenAI key
  is configured (never the key itself).
- Toggle between Ollama and OpenAI — the choice persists across restarts. Switching to
  OpenAI is blocked if no `OPENAI_API_KEY` is configured.
- Change models via Doppler/env vars and restart — see [Configuration & sécurité](#configuration--sécurité-doppler).

---

## Project structure

```
RAG-Bachelor/
├── .devcontainer/
│   └── devcontainer.json         # VS Code Dev Container (builds from Dockerfile)
├── Dockerfile                    # App image — Python 3.13 + all deps + Doppler CLI
├── docker/entrypoint.sh          # doppler run -- wrapper (falls back to no-Doppler for local dev)
├── pyproject.toml                # Python dependencies + tool config
│
├── data/
│   ├── pdfs/                     # ← Drop your PDF files here
│   ├── chroma/                   # Vector index (auto-created on first index)
│   └── app.db                    # SQLite study DB (auto-created)
│
└── src/rag_bachelor/
    ├── config.py                 # All settings (pydantic-settings)
    ├── ingest/
    │   ├── extract.py            # PyMuPDF → pages + empty-page detection
    │   ├── chunk.py              # Recursive text splitter (~900 chars, 150 overlap)
    │   └── index.py              # ChromaDB upsert / query helpers
    ├── core/
    │   ├── embeddings.py         # BAAI/bge-m3 local embeddings
    │   ├── retriever.py          # Semantic search (cosine similarity)
    │   ├── llm.py                # Ollama + OpenAI providers (toggle persisted in SQLite)
    │   ├── qa.py                 # RAG Q&A with French system prompt + citations
    │   └── questions.py          # Easy / medium / hard question generation
    ├── study/
    │   ├── srs.py                # SM-2 spaced-repetition algorithm
    │   ├── store.py              # SQLite persistence (cards + reviews)
    │   └── stats.py              # Per-topic mastery statistics
    └── app/
        └── web/
            ├── server.py         # FastAPI entry point, lifespan, router wiring
            ├── _deps.py          # Shared Jinja2 templates + sidebar_ctx()
            ├── routes/           # One APIRouter per tab
            ├── templates/        # Jinja2 HTML templates + partials/
            └── static/           # htmx.min.js, app.css (vendored)
```

---

## Development

```bash
# Run tests (32 tests)
pytest

# Lint
ruff check src/ tests/

# Type check
mypy src/

# Auto-reload on file changes (local dev)
uvicorn rag_bachelor.app.web.server:app --port 8090 --reload
```

**Changing the Ollama model:**  
Set `OLLAMA_MODEL` (via Doppler or your environment), then pull the model: `ollama pull <model-name>`.

**Changing the embedding model:**  
Set `EMBEDDING_MODEL`, delete `data/chroma/`, and re-index all PDFs.  
Vectors from different models are incompatible — re-indexing is required.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| *"Aucun document indexé"* in Q&A tab | Go to 📚 Documentation → (Re)indexer |
| Slow first container start | bge-m3 model downloading (~1.2 GB) — fast on subsequent starts |
| Ollama error / no response | Run `ollama serve` and `ollama list` to check the model is pulled |
| Dev Container can't reach Ollama on Mac | Set `OLLAMA_HOST=http://host.docker.internal:11434` |
| Port 8090 already in use | Kill other uvicorn processes (`pkill -f uvicorn`), or change `--port` |
| Blank pages not indexed | Expected — pages with no text layer are skipped with a warning |

---

## Tech stack

| Concern | Choice |
|---|---|
| UI | FastAPI + Jinja2 + HTMX |
| PDF extraction | PyMuPDF |
| Embeddings | sentence-transformers + BAAI/bge-m3 (local, multilingual) |
| Vector store | ChromaDB (persistent, embedded) |
| LLM | Ollama qwen2.5:7b-instruct |
| Config | pydantic-settings |
| Study DB | SQLite (stdlib) |
| Spaced repetition | SM-2 (custom implementation) |
