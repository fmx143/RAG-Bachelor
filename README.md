# 🎓 RAG-Bachelor — Assistant de révision local

A local-first RAG study assistant for French bachelor PDF documents.  
Ask questions about your courses, generate easy/medium/hard revision questions, and track your progress with spaced repetition — LLM via Ollama Cloud or OpenAI.

---

## Features

| | |
|---|---|
| 📚 **Document management** | Upload PDFs (auto-indexed, duplicates skipped) into a local vector store, re-index or remove them |
| ❓ **Q&A with citations** | Ask anything in French, get a sourced answer with file name + page number |
| 🔄 **Spaced repetition** | SM-2 algorithm (Anki-style) — review due cards, self-grade, auto-reschedule |
| 🎯 **Question generation** | LLM-generated easy / medium / hard questions per topic, add them to your deck |
| 🏦 **Question bank** | Generate a whole-document bank of free / QCM (single/multi) / Vrai-Faux questions, semantic near-duplicate filtering, filter by difficulty/type/result, auto-graded structured revision |
| 📊 **Progress tracking** | Per-topic mastery bars, weak vs strong subject overview |
| ⚙️ **LLM & data** | Ollama Cloud or OpenAI for the LLM (provider and model chosen in Settings) — bge-m3 embeddings are always local |
| 🔒 **Secrets via Doppler** | No API keys or passwords ever live in a `.env` file or the image — see [Configuration & sécurité](#configuration--sécurité-doppler) |

---

## Requirements

| Tool | Version | Purpose |
|---|---|---|
| Python | ≥ 3.13 | Local dev |
| Docker Desktop | any recent | VS Code Dev Container |
| VS Code + Dev Containers extension | any | Container-based dev on Mac / WSL |
| Ollama Cloud key (`OLLAMA_API_KEY`) or OpenAI key | — | LLM — no local Ollama needed |

---

## Option A — VS Code Dev Container (recommended for Mac / WSL)

This is the zero-setup path. VS Code builds the Docker image from the `Dockerfile`
and mounts your local `data/` folder so everything persists across rebuilds.

### 1. Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) running
- VS Code with the **Dev Containers** extension (`ms-vscode-remote.remote-containers`)

### 2. Configure

No `.env` file is needed. The LLM needs an API key (`OLLAMA_API_KEY` for Ollama Cloud
and/or `OPENAI_API_KEY`), plus `APP_PASSWORD` if you want the login gate — provide them
through Doppler, see [Configuration & sécurité (Doppler)](#configuration--sécurité-doppler) below.

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

# 3. Launch without secrets (UI works, LLM calls fail until a key is set)
uvicorn rag_bachelor.app.web.server:app --host 0.0.0.0 --port 8090
# or: rag-web   (after pip install -e .)
```

To get working answers, launch through Doppler so `OLLAMA_API_KEY` is injected — see
[Local dev with Doppler](#local-dev-with-doppler) below.

Open **http://localhost:8090**.

---

## Configuration & sécurité (Doppler)

This app takes **no `.env` file** — there is nothing to copy, and nothing sensitive
ever lives on disk. Settings are read from real process environment variables
(`pydantic-settings` in `config.py`), and [Doppler](https://doppler.com) is how those
variables get there, both locally and on the NAS. Without an
`OLLAMA_API_KEY` or `OPENAI_API_KEY` the app starts, but LLM calls (answers, questions)
fail; without `APP_PASSWORD` there is no login gate.

### Secret names

| Variable | Required for | Notes |
|---|---|---|
| `OLLAMA_API_KEY` | Ollama Cloud provider | Sent as an explicit `Authorization` header; never logged or templated |
| `OLLAMA_MODEL` | Ollama Cloud provider | Default model; the one chosen in ⚙️ Paramètres takes precedence |
| `OPENAI_API_KEY` | Optional OpenAI provider | Only read at request time; never logged, templated, or echoed back on error |
| `OPENAI_MODEL` | Optional OpenAI provider | e.g. `gpt-4o-mini` |
| `DEFAULT_LLM_PROVIDER` | Optional | `ollama` (cloud, default) or `openai` — startup default; toggle in ⚙️ Paramètres overrides it afterwards |
| `APP_PASSWORD` | Login gate | Empty/unset ⇒ gate disabled (fine for local dev, **required** before exposing the app publicly) |
| `SESSION_SECRET` | Login gate | Required whenever `APP_PASSWORD` is set — signs the session cookie; app refuses to start otherwise |
| `SESSION_COOKIE_SECURE` | Login gate over HTTPS | Set `true` once served through the Cloudflare Tunnel (TLS terminates there) |
| `POSTGRES_HOST` / `_PORT` / `_DB` / `_USER` / `_PASSWORD` | Isolated data tier (NAS) | Empty `POSTGRES_HOST` ⇒ falls back to local SQLite (`data/app.db`) |
| `CHROMA_HOST` / `CHROMA_PORT` | Isolated data tier (NAS) | Empty `CHROMA_HOST` ⇒ falls back to the local embedded ChromaDB (`data/chroma`) |

### Local dev with Doppler

`rag-web` is installed **inside the project venv**: activate it first, otherwise Doppler fails with
`exec: "rag-web": executable file not found in $PATH`.

```bash
# Once per machine
doppler login
doppler setup                    # links this directory to a Doppler project/config
doppler secrets set OLLAMA_API_KEY      # add OPENAI_API_KEY / APP_PASSWORD / SESSION_SECRET if needed

# Every session
cd RAG-Bachelor
source .venv/bin/activate        # Windows: .venv\Scripts\activate
doppler run -- rag-web           # or: doppler run -- .venv/bin/rag-web (no activation needed)
```

Then open **http://localhost:8090** and check ⚙️ Paramètres:

1. The Ollama radio button is enabled (key detected) and the model field suggests the models
   listed by ollama.com. If it stays greyed out, the key did not reach the app.
2. Pick the provider and a model, click **Enregistrer** / **Enregistrer les modèles**.
3. Ask a question in the **Question** tab (an indexed document is required). A wrong model
   name shows up here as a generic error — pick a name from the list.

**Trying the app without touching your real data.** By default the app reads and writes
`data/` (PDFs, `data/chroma/`, `data/app.db`). To test on a throw-away copy, point the
paths elsewhere; they are ordinary settings:

```bash
PDFS_DIR=/tmp/rag-test/pdfs CHROMA_DIR=/tmp/rag-test/chroma DB_PATH=/tmp/rag-test/app.db \
  doppler run -- rag-web
```

Upload a small PDF in 📚 Documentation (it is indexed automatically), then ask a question about it.
(`DB_PATH` applies only while `POSTGRES_HOST` is unset; `CHROMA_DIR` only while `CHROMA_HOST` is unset.)

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
directly without Doppler (no secrets, no login gate) — useful for a plain local
`docker compose up` with no secrets involved.

### Defense in depth

Put [Cloudflare Access](https://developers.cloudflare.com/cloudflare-one/policies/access/)
in front of the tunnel as a second layer on top of the app's own login gate. Scope the Access
policy to your exact email (not just "any Google account") — otherwise anyone with a Google
login passes the 2FA check, not just you.

> **Embeddings are always local** (BAAI/bge-m3) — no key needed for that part regardless.

### NAS deployment topology (2-tier + Cloudflare edge)

`docker-compose.yml` splits the app from an isolated data tier:

- **`app`** — FastAPI/HTMX (front + back in one process, they aren't separable without
  abandoning the HTMX server-rendered pattern). The only service with a published port.
- **`postgres` + `chroma`** — the data tier. Both sit on a Docker network marked `internal:
  true`: no published ports, no route to the internet, reachable only from `app`. This is
  what makes the tier boundary a real security boundary rather than just a folder layout —
  a compromised `app` container can still query the data through its scoped DB credentials,
  but can't reach the database engines' filesystem, and the databases themselves can't be
  reached from the LAN or exfiltrate anything directly to the internet.

Cloudflare Tunnel/Access is the edge in front of `app` and is **not** one of these tiers —
it terminates entirely on Cloudflare's side before traffic ever reaches the NAS.

```bash
# Add POSTGRES_PASSWORD alongside the other secrets:
doppler secrets set POSTGRES_PASSWORD OPENAI_API_KEY APP_PASSWORD SESSION_SECRET

# Launch — Doppler populates POSTGRES_PASSWORD for docker-compose's ${...} substitution
# too, so it never lands in a .env file on the NAS:
doppler run -- docker compose up -d --build
```

Point `cloudflared` (run separately — it isn't part of this compose file) at
`http://localhost:8090`.

---

## Setting up Ollama Cloud

Create an API key on [ollama.com](https://ollama.com), then expose it as `OLLAMA_API_KEY`
(Doppler or environment). In ⚙️ Paramètres, pick "Ollama (cloud)" and a model from the list
fetched from ollama.com. There is no local Ollama mode anymore.

---

## Using the app

### 1 — Add and index your PDFs

**Tab: 📚 Documentation**

1. Drag and drop your PDFs onto the **upload area** — new files are saved to `data/pdfs/`
   and **indexed automatically** (progress bar).
2. A file whose **name or content** already exists is skipped with a message — no duplicate,
   no overwrite. If an indexing job is already running, the file is saved and you click
   **Indexer** next to it once the job is done.
3. The chunk count updates after indexing. A warning appears for blank/image-only pages,
   and for PDFs with no extractable text (scans).
4. To force a re-index, click **🔄 (Re)indexer tous les PDFs**, or **Indexer** next to a file.
5. To remove a document, click 🗑️ — it is deleted from disk **and** from the index.

> **Replacing a PDF:** upload skips an existing name, so delete the old file first (or
> replace it in `data/pdfs/`), then upload / click **Indexer** — old chunks are removed automatically.

> **⏱️ Indexing is slow.** Embedding runs locally on CPU with bge-m3 (568M params,
> no GPU). Measured on a 10-core ARM container:
>
> | | |
> |---|---|
> | Throughput | ~1.8 chunks/s |
> | A 150-page PDF (~250 chunks) | ~2 min |
> | 4 PDFs (~530 chunks total) | ~5 min |
>
> Indexing runs in the background: the click returns immediately and a progress
> bar (file N/M, chunks done/total) polls every 2 s until it finishes — safe behind
> the Cloudflare Tunnel, no 524, and you can navigate away or reload `/docs` without
> losing the job.

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

### 4 — Generate a question bank for a whole document

**Tab: 🏦 Banque**

1. Pick an indexed **document**, a **question type** (libre, QCM simple, QCM multiple,
   Vrai-Faux), and a target count (1–100).
2. Click **Générer** — the app walks the document window by window (a few chunks at a
   time), asking the LLM for questions grounded in each window, until the target is
   reached. A progress bar polls itself until the job finishes; click **Arrêter** to stop early.
3. Near-duplicate questions are filtered out automatically via embedding similarity
   against everything already in the bank for that source.
4. Filter the resulting list by **difficulty**, **type**, and **result** (correct /
   incorrect / untried), search by text, add individual questions (or **Tout ajouter**)
   to your revision deck, or delete selected ones.

---

### 5 — Revise with spaced repetition

**Tab: 🔄 Révision**

Cards due today are shown one at a time. Free-text cards: click **👁️ Afficher la réponse**
when ready, then grade yourself:

| Button | Effect |
|---|---|
| 😰 Raté / 🤔 Difficile | Resets the card — back to 1 day |
| 😊 Bien / 🌟 Parfait | Advances the card — interval grows via SM-2 |

Structured cards (QCM/Vrai-Faux) added from the 🏦 Banque are **auto-graded**: pick your
answer(s), the app compares them to the stored correct set and applies the matching SM-2
grade automatically — no self-assessment, no LLM call.

---

### 6 — Track your progress

**Tab: 📊 Progrès**

- **Summary metrics:** total cards, due today, average ease factor.
- **Per-topic mastery bars** (weakest first):
  - 🔴 < 40% — needs work · 🟡 40–70% — progressing · 🟢 > 70% — mastered
- **À renforcer / Points forts** columns for a quick overview.

---

### 7 — Settings

**Tab: ⚙️ Paramètres**

- View the active LLM provider (Ollama Cloud / OpenAI), its model, and whether each key
  is configured (never the key itself).
- Toggle between Ollama Cloud and OpenAI — the choice persists across restarts. Switching
  to a provider is blocked if its key (`OLLAMA_API_KEY` / `OPENAI_API_KEY`) is missing.
- Choose the **model** of each provider (persisted). The Ollama field suggests the models
  listed by ollama.com. To compare answers, switch provider or model and ask the same question again.
- API keys still come from Doppler/env vars — see [Configuration & sécurité](#configuration--sécurité-doppler).

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
    │   ├── llm.py                # Ollama Cloud + OpenAI providers (provider/model persisted)
    │   ├── qa.py                 # RAG Q&A with French system prompt + citations
    │   ├── questions.py          # Easy / medium / hard question generation (per-topic)
    │   ├── qtypes.py             # Question types (free/mcq_single/mcq_multi/tf), JSON parsing
    │   └── bank.py               # Whole-document question-bank generation + semantic dedup
    ├── study/
    │   ├── srs.py                # SM-2 spaced-repetition algorithm
    │   ├── store.py              # SQLite/PostgreSQL persistence (cards + reviews + question_bank)
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
# Run tests (91 tests)
pytest

# Lint
ruff check src/ tests/

# Type check
mypy src/

# Auto-reload on file changes (local dev)
uvicorn rag_bachelor.app.web.server:app --port 8090 --reload
```

**Changing the LLM model:**  
Pick it in ⚙️ Paramètres (persisted), or set `OLLAMA_MODEL` for the default.

**Changing the embedding model:**  
Set `EMBEDDING_MODEL`, delete `data/chroma/`, and re-index all PDFs.  
Vectors from different models are incompatible — re-indexing is required.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| *"Aucun document indexé"* in Q&A tab | Go to 📚 Documentation → (Re)indexer |
| Slow first container start | bge-m3 model downloading (~1.2 GB) — fast on subsequent starts |
| Indexing progress bar runs for minutes | Expected — ~1.8 chunks/s on CPU, ~2 min per 150-page PDF. It runs in the background, no 524. See [Add and index your PDFs](#1--add-and-index-your-pdfs) |
| Ollama error / no response | Check `OLLAMA_API_KEY` and that the model name exists in the ⚙️ Paramètres list |
| Port 8090 already in use | Kill other uvicorn processes (`pkill -f uvicorn`), or change `--port` |
| Blank pages not indexed | Expected — pages with no text layer are skipped with a warning |

### Upgrading Chroma

The Chroma server image (pinned by digest in `docker-compose.yml`) and the `chromadb` client
(pinned in `pyproject.toml`) must be upgraded **together**. Back up the `chroma_data` volume
first, and test the new versions on a copy of it before touching production.

Compatibility verified in production on 2026-10-04: client `chromadb` 1.5.9 (container
`rag-bachelor-app-1`) against the server image pinned by digest.

---

## Tech stack

| Concern | Choice |
|---|---|
| UI | FastAPI + Jinja2 + HTMX |
| PDF extraction | PyMuPDF |
| Embeddings | sentence-transformers + BAAI/bge-m3 (local, multilingual) |
| Vector store | ChromaDB (persistent, embedded) |
| LLM | Ollama Cloud or OpenAI (chosen in Settings) |
| Config | pydantic-settings |
| Study DB | SQLite (stdlib) |
| Spaced repetition | SM-2 (custom implementation) |
