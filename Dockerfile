FROM python:3.13-slim

# Build tools needed by some sentence-transformers wheels + curl for healthcheck
# + gnupg/apt-transport-https for the Doppler CLI apt repo below
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        g++ \
        curl \
        gnupg \
        apt-transport-https \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Doppler CLI — injects secrets at runtime via `doppler run --` (docker/entrypoint.sh).
# Only a scoped, revocable DOPPLER_TOKEN ever needs to reach this container; the real
# secrets (OPENAI_API_KEY, APP_PASSWORD, ...) never touch the image or filesystem.
RUN curl -sLf --retry 3 --tlsv1.2 --proto "=https" \
        'https://packages.doppler.com/public/cli/gpg.DE2A7741A397C129.key' \
        | gpg --dearmor -o /usr/share/keyrings/doppler-archive-keyring.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/doppler-archive-keyring.gpg] https://packages.doppler.com/public/cli/deb/debian any-version main" \
        > /etc/apt/sources.list.d/doppler-cli.list \
    && apt-get update && apt-get install -y --no-install-recommends doppler \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install deps first (cacheable layer — only rebuilds when pyproject.toml changes)
COPY pyproject.toml .
# Create a minimal package stub so hatchling can install in editable mode
RUN mkdir -p src/rag_bachelor && touch src/rag_bachelor/__init__.py
RUN pip install --no-cache-dir -e ".[dev]"

# Copy source (separate layer so dep installs aren't invalidated by code changes)
COPY src/ src/
COPY docker/entrypoint.sh docker/entrypoint.sh
RUN chmod +x docker/entrypoint.sh

# Runtime directories (actual data is mounted as volumes — see docker-compose.yml)
RUN mkdir -p data/pdfs data/chroma model_cache

# Embedding model cache — bge-m3 is ~1.2 GB; mount as a named volume so it
# survives container rebuilds and is shared between dev containers.
ENV HF_HOME=/app/model_cache
ENV SENTENCE_TRANSFORMERS_HOME=/app/model_cache

EXPOSE 8090

HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \
    CMD curl -f http://localhost:8090/ || exit 1

ENTRYPOINT ["docker/entrypoint.sh"]
CMD ["uvicorn", "rag_bachelor.app.web.server:app", \
     "--host", "0.0.0.0", "--port", "8090"]
