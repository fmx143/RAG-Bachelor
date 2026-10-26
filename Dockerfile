# ── Build stage: compilers, apt keys and pip caches stay here ──────────────────
# Distro pinned (trixie); for fully reproducible builds pin the digest too:
#   docker buildx imagetools inspect python:3.13-slim-trixie
FROM python:3.13-slim-trixie AS build

# Build tools needed by some sentence-transformers wheels + gnupg for the Doppler apt repo
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        g++ \
        curl \
        gnupg \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Doppler CLI (a static binary, copied into the runtime stage) — injects secrets at runtime
# via `doppler run --` (docker/entrypoint.sh). Only a scoped, revocable Doppler service token
# ever reaches the container; the real secrets never touch the image or filesystem.
RUN curl -sLf --retry 3 --tlsv1.2 --proto "=https" \
        'https://packages.doppler.com/public/cli/gpg.DE2A7741A397C129.key' \
        | gpg --dearmor -o /usr/share/keyrings/doppler-archive-keyring.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/doppler-archive-keyring.gpg] https://packages.doppler.com/public/cli/deb/debian any-version main" \
        > /etc/apt/sources.list.d/doppler-cli.list \
    && apt-get update && apt-get install -y --no-install-recommends doppler \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH

COPY pyproject.toml .
# Minimal package stub so hatchling can install in editable mode (real src/ comes later)
RUN mkdir -p src/rag_bachelor && touch src/rag_bachelor/__init__.py
# CPU-only torch AND torchvision (pulled by Docling) first: the default PyPI wheels bundle
# CUDA (several GB) and the NAS has no GPU. No [dev] extra — pytest/ruff/mypy run outside.
RUN pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -e .

# ── Runtime stage ──────────────────────────────────────────────────────────────
FROM python:3.13-slim-trixie

WORKDIR /app

COPY --from=build /usr/bin/doppler /usr/bin/doppler
COPY --from=build /opt/venv /opt/venv
# Model cache (bge-m3, reranker, Docling layout/table models) is a named volume.
# The root filesystem is read-only at runtime (docker-compose.yml), so HOME points to the
# /tmp tmpfs: tool caches (Doppler fallback file, torch, ...) land there.
ENV PATH=/opt/venv/bin:$PATH \
    HF_HOME=/app/model_cache \
    SENTENCE_TRANSFORMERS_HOME=/app/model_cache \
    HF_HUB_DISABLE_TELEMETRY=1 \
    HOME=/tmp

# Non-root runtime user (fixed UID). Only the dirs the app writes to are its own;
# created before the reranker download so the 2.2 GB layer isn't duplicated by a chown -R.
RUN useradd --system --uid 10001 --no-create-home app \
    && mkdir -p data/pdfs data/chroma model_cache \
    && chown app:app data data/pdfs data/chroma model_cache

# Bake the reranker (~2.2 GB) into the image so the first question doesn't download it.
# Own layer right after the deps: code changes below don't re-trigger the download.
# Caveat: a model_cache volume that already exists shadows this copy (Docker only seeds
# an empty volume), so on an existing NAS volume it downloads once at first use instead —
# as do the Docling models (~700 MB, model_cache/docling) on the first indexing.
USER 10001
RUN python -c "from sentence_transformers import CrossEncoder; CrossEncoder('BAAI/bge-reranker-v2-m3', cache_folder='model_cache')"

# Copy source (separate layer so dep installs aren't invalidated by code changes).
# Root-owned on purpose: the app can read its code but not rewrite it.
USER root
COPY src/ src/
COPY docker/entrypoint.sh docker/entrypoint.sh
RUN chmod 755 docker/entrypoint.sh

USER 10001

EXPOSE 8090

# Single source of truth for the container's health (docker-compose.yml does not redefine it).
# /healthz checks Postgres + Chroma, so a dead data tier turns the container unhealthy.
# Python instead of curl: no extra package in the runtime image. urlopen raises on a 503.
HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/healthz', timeout=5)"]

ENTRYPOINT ["docker/entrypoint.sh"]
CMD ["uvicorn", "rag_bachelor.app.web.server:app", \
     "--host", "0.0.0.0", "--port", "8090"]
