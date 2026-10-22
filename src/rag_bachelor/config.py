"""Application-wide configuration loaded from environment / .env file."""

from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Paths ──────────────────────────────────────────────────────────────
    data_dir: Path = Path("data")
    pdfs_dir: Path = Path("data/pdfs")
    chroma_dir: Path = Path("data/chroma")
    db_path: Path = Path("data/app.db")

    # ── PostgreSQL (optional — isolated data tier for a NAS deployment) ────
    # Empty POSTGRES_HOST ⇒ falls back to the local SQLite file above, so
    # local dev without Docker stays frictionless.
    postgres_host: str = ""
    postgres_port: int = 5432
    postgres_db: str = "rag_bachelor"
    postgres_user: str = "rag_bachelor"
    postgres_password: SecretStr = SecretStr("")

    # ── ChromaDB server (optional — isolated data tier for a NAS deployment)
    # Empty CHROMA_HOST ⇒ falls back to the local PersistentClient (chroma_dir).
    chroma_host: str = ""
    chroma_port: int = 8000

    # ── Embedding ─────────────────────────────────────────────────────────
    # bge-m3 is the primary model (strong French/multilingual, ~1.2 GB).
    # Lighter alternative: "intfloat/multilingual-e5-base"
    embedding_model: str = "BAAI/bge-m3"
    # Where to cache HuggingFace models. In Docker this is overridden via env
    # to point to a named volume. "model_cache" works for local dev.
    embedding_cache_dir: str = "model_cache"

    # ── Chunking ──────────────────────────────────────────────────────────
    chunk_size: int = 900
    chunk_overlap: int = 150

    # ── Retrieval ─────────────────────────────────────────────────────────
    retrieval_top_k: int = 5
    # Cross-encoder reranker (~2.2 GB, ~6 s/search on CPU); empty string disables it.
    reranker_model: str = "BAAI/bge-reranker-v2-m3"
    rerank_pool: int = 10  # fused candidates handed to the reranker
    # HippoRAG-style fact graph (core/graph.py): a 3rd ranking in the retrieval fusion.
    # Building it costs one LLM call per chunk, so it is a per-document action in 📚 Documentation.
    graph_enabled: bool = True
    graph_auto_build: bool = True  # also build it right after a document is indexed
    # max searches per agentic answer (reranker ≈ 6 s each on CPU); bounded, each costs an LLM call
    agent_max_steps: int = Field(default=3, ge=1, le=6)

    # ── Ollama Cloud ──────────────────────────────────────────────────────
    # Same SecretStr rule as the OpenAI key: only read at client construction.
    ollama_host: str = "https://ollama.com"
    ollama_api_key: SecretStr = SecretStr("")
    ollama_model: str = "deepseek-v4.1-flash"  # unverified name — pick from Settings list
    ollama_vision_model: str = "llava:7b"

    # ── OpenAI (optional cloud provider, manual toggle in Settings) ────────
    # Never render these via .get_secret_value() outside of client construction —
    # SecretStr keeps them out of repr()/logs/template output by default.
    openai_api_key: SecretStr = SecretStr("")
    openai_model: str = "gpt-4o-mini"
    default_llm_provider: str = "ollama"

    # ── Auth (shared-secret login gate — required once exposed publicly) ───
    app_password: SecretStr = SecretStr("")
    session_secret: SecretStr = SecretStr("")
    # False for local http:// dev; set true (via Doppler) once served over
    # https through the Cloudflare Tunnel, so the session cookie gets Secure.
    session_cookie_secure: bool = False

    # ── Server ───────────────────────────────────────────────────────────
    app_port: int = 8090


settings = Settings()
