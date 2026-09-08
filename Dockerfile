# Image for the background services (ingest, debouncer, state engine, API,
# dashboard, sync -- the command is chosen per service in docker-compose.yml).
# The simulator and the seeding script are run from your own machine with
# `uv run`, so they are not what this image is for.

FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.8.17 /uv /usr/local/bin/uv

WORKDIR /app

# uv gives a download 30 seconds by default, which is not enough on a slow
# or contended connection -- the kind this project assumes (SPEC.md 2.4).
# A timeout here fails the build for a reason that has nothing to do with
# the code.
ENV UV_HTTP_TIMEOUT=180

# Dependencies first, and WITHOUT our own source, so that editing a Python
# file does not throw this layer away and re-download every dependency.
# Docker caches by the files copied, so this step only re-runs when
# pyproject.toml or uv.lock actually change.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

# Now the source. Everything below here re-runs on a code change, which is
# fine: installing just this project takes about a second.
COPY src/ ./src/
COPY config/ ./config/
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"

# Unbuffered, so `docker compose logs` shows output as it happens rather
# than in silent chunks.
ENV PYTHONUNBUFFERED=1

CMD ["tasker-ingest"]
