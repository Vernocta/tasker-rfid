# Image for the background services (currently just ingest).
# The simulator and the seeding script are run from your own machine with
# `uv run`, so they are not what this image is for.

FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.8.17 /uv /usr/local/bin/uv

WORKDIR /app

# Dependencies first, so editing source does not reinstall the world.
COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/
COPY config/ ./config/

RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"

# Unbuffered, so `docker compose logs` shows output as it happens rather
# than in silent chunks.
ENV PYTHONUNBUFFERED=1

CMD ["tasker-ingest"]
