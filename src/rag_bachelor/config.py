"""Application-wide configuration loaded from environment / .env file."""

from pathlib import Path

from pydantic import SecretStr
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

    # ── Ollama ────────────────────────────────────────────────────────────
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:7b-instruct"

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
