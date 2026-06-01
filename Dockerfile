FROM python:3.13-slim

# Build tools needed by some sentence-transformers wheels + curl for healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        g++ \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install deps first (cacheable layer — only rebuilds when pyproject.toml changes)
COPY pyproject.toml .
# Create a minimal package stub so hatchling can install in editable mode
RUN mkdir -p src/rag_bachelor && touch src/rag_bachelor/__init__.py
RUN pip install --no-cache-dir -e ".[dev]"

# Copy source (separate layer so dep installs aren't invalidated by code changes)
COPY src/ src/

# Runtime directories (actual data is mounted as volumes — see docker-compose.yml)
RUN mkdir -p data/pdfs data/chroma model_cache

# Embedding model cache — bge-m3 is ~1.2 GB; mount as a named volume so it
# survives container rebuilds and is shared between dev containers.
ENV HF_HOME=/app/model_cache
ENV SENTENCE_TRANSFORMERS_HOME=/app/model_cache

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \
    CMD curl -f http://localhost:8501/_stcore/health || exit 1

CMD ["streamlit", "run", "src/rag_bachelor/app/main.py", \
     "--server.address=0.0.0.0", \
     "--server.port=8501", \
     "--server.headless=true", \
     "--browser.gatherUsageStats=false"]
