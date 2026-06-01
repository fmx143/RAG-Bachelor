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
| ⚙️ **Auto online/offline** | OpenAI when online, Ollama when offline — embeddings always local |

---

## Requirements

| Tool | Version | Purpose |
|---|---|---|
| Python | ≥ 3.13 | Local dev |
| Docker Desktop | any recent | VS Code Dev Container |
| VS Code + Dev Containers extension | any | Container-based dev on Mac / WSL |
| Ollama | latest | Local LLM for offline mode |
| OpenAI API key | — | Online mode (optional) |

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

```bash
cp .env.example .env
# Open .env and fill in at minimum:
#   OPENAI_API_KEY=sk-...          (optional — leave blank for offline-only)
#   OLLAMA_HOST=http://host.docker.internal:11434   (default for Mac)
```

### 3. Open in container

- Open the `RAG-Bachelor` folder in VS Code.
- A notification pops up: **"Reopen in Container"** — click it.
- Alternatively: `Ctrl+Shift+P` → **Dev Containers: Reopen in Container**.

VS Code builds the image (first time ~5 min, mostly downloading PyTorch), installs
Python/Ruff extensions, and **automatically starts Streamlit** on port 8501.

### 4. Open the app

The browser opens automatically at **http://localhost:8501**.  
If it doesn't, open it manually or look in the VS Code **Ports** panel.

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

# 3. Configure
cp .env.example .env
# Edit .env

# 4. Launch
streamlit run src/rag_bachelor/app/main.py
```

Open **http://localhost:8501**.

---

## Configuration (`.env`)

Copy `.env.example` to `.env` before starting. Key settings:

```bash
# ── Online mode ───────────────────────────────────────────────────────────────
OPENAI_API_KEY=sk-...          # Leave blank to use Ollama-only mode

# ── Offline mode (Ollama) ─────────────────────────────────────────────────────
# Mac + Dev Container:  host.docker.internal reaches Ollama on your Mac host
OLLAMA_HOST=http://host.docker.internal:11434
# Local Python on any OS:
# OLLAMA_HOST=http://localhost:11434

# ── Force a specific provider (optional) ─────────────────────────────────────
# FORCE_PROVIDER=            # empty = auto-detect (recommended)
# FORCE_PROVIDER=openai      # always use OpenAI
# FORCE_PROVIDER=ollama      # always use Ollama
```

The app **auto-detects** internet connectivity: OpenAI when reachable (and a key is set),
Ollama otherwise. Override with `FORCE_PROVIDER`.

> **Embeddings are always local** (BAAI/bge-m3). Switching providers never requires re-indexing.

---

## Setting up Ollama (offline / local LLM)

### macOS

```bash
# Install (or download from https://ollama.com/download)
brew install ollama

# Start the server
ollama serve

# Pull the model the app uses
ollama pull qwen2.5:7b-instruct
```

Set `OLLAMA_HOST=http://host.docker.internal:11434` in `.env` when running inside the Dev Container,
or `http://localhost:11434` when running locally.

### Linux / WSL

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen2.5:7b-instruct
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

- View active provider (online/offline), connectivity, and API key status.
- Click **🔄 Vérifier la connexion** to force a fresh check.
- Change models in `.env` and restart — see notes below.

---

## Project structure

```
RAG-Bachelor/
├── .devcontainer/
│   └── devcontainer.json         # VS Code Dev Container (builds from Dockerfile)
├── Dockerfile                    # App image — Python 3.13 + all deps
├── pyproject.toml                # Python dependencies + tool config
├── .env.example                  # Configuration template → copy to .env
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
    │   ├── llm.py                # OpenAI / Ollama provider + auto-detect
    │   ├── qa.py                 # RAG Q&A with French system prompt + citations
    │   └── questions.py          # Easy / medium / hard question generation
    ├── study/
    │   ├── srs.py                # SM-2 spaced-repetition algorithm
    │   ├── store.py              # SQLite persistence (cards + reviews)
    │   └── stats.py              # Per-topic mastery statistics
    └── app/
        ├── main.py               # Streamlit entry point + sidebar
        └── tabs/                 # One module per tab (render() function)
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
streamlit run src/rag_bachelor/app/main.py --server.runOnSave=true
```

**Changing the Ollama model:**  
Edit `OLLAMA_MODEL` in `.env`, then pull the model: `ollama pull <model-name>`.

**Changing the embedding model:**  
Edit `EMBEDDING_MODEL` in `.env`, delete `data/chroma/`, and re-index all PDFs.  
Vectors from different models are incompatible — re-indexing is required.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| *"Aucun document indexé"* in Q&A tab | Go to 📚 Documentation → (Re)indexer |
| Slow first container start | bge-m3 model downloading (~1.2 GB) — fast on subsequent starts |
| Ollama error / no response | Run `ollama serve` and `ollama list` to check the model is pulled |
| App shows "Ollama (hors ligne)" when online | Check `OPENAI_API_KEY` in `.env`; click 🔄 in ⚙️ Paramètres |
| Dev Container can't reach Ollama on Mac | Set `OLLAMA_HOST=http://host.docker.internal:11434` in `.env` |
| Port 8501 already in use | Kill other Streamlit instances, or change port in `devcontainer.json` |
| Blank pages not indexed | Expected — pages with no text layer are skipped with a warning |

---

## Tech stack

| Concern | Choice |
|---|---|
| UI | Streamlit |
| PDF extraction | PyMuPDF |
| Embeddings | sentence-transformers + BAAI/bge-m3 (local, multilingual) |
| Vector store | ChromaDB (persistent, embedded) |
| Online LLM | OpenAI gpt-4o-mini / gpt-4o |
| Offline LLM | Ollama qwen2.5:7b-instruct |
| Config | pydantic-settings |
| Study DB | SQLite (stdlib) |
| Spaced repetition | SM-2 (custom implementation) |
