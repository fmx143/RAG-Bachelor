#!/bin/sh
# Injects secrets from Doppler when a token is available, otherwise runs the
# command directly (keeps plain local `docker compose up` frictionless).
#
# DOPPLER_TOKEN_FILE (Docker-secret style mount) is preferred over a plain
# DOPPLER_TOKEN env var, since env vars are visible via `docker inspect`/`ps`.
set -eu

if [ -n "${DOPPLER_TOKEN_FILE:-}" ] && [ -f "$DOPPLER_TOKEN_FILE" ]; then
    DOPPLER_TOKEN="$(cat "$DOPPLER_TOKEN_FILE")"
    export DOPPLER_TOKEN
fi

if [ -n "${DOPPLER_TOKEN:-}" ]; then
    exec doppler run -- "$@"
fi

echo "entrypoint: no DOPPLER_TOKEN/DOPPLER_TOKEN_FILE set — starting without Doppler (local dev only)." >&2
exec "$@"
