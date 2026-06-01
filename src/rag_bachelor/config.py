"""Application-wide configuration loaded from environment / .env file."""

from pathlib import Path

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

    # ── OpenAI ────────────────────────────────────────────────────────────
    openai_api_key: str = ""
    openai_model_default: str = "gpt-4o-mini"
    openai_model_hard: str = "gpt-4o"

    # ── Ollama ────────────────────────────────────────────────────────────
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:7b-instruct"

    # ── Provider override ─────────────────────────────────────────────────
    # "" = auto-detect; "openai" or "ollama" = force a specific provider.
    force_provider: str = ""

    # ── Connectivity check ────────────────────────────────────────────────
    connectivity_timeout: float = 3.0
    # Cache TTL in seconds: how long to remember the last connectivity result
    connectivity_cache_ttl: int = 30


settings = Settings()
