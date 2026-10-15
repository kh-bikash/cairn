# syntax=docker/dockerfile:1.7
# Hosted API image (Railway, Fly, ...). Built from the repo root so it can ship the demo snapshot and the
# Orbiton workspace; on first start it builds both graphs, on later starts it just serves.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY --from=ghcr.io/astral-sh/uv:0.11.12 /uv /usr/local/bin/uv
WORKDIR /app

COPY backend/pyproject.toml backend/uv.lock backend/README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY backend/src ./src
RUN uv sync --frozen --no-dev

COPY demo ./demo
COPY workspaces ./workspaces

RUN useradd --create-home --uid 10001 cairn && mkdir -p /app/data && chown -R cairn /app/data
USER cairn

ENV PATH="/app/.venv/bin:$PATH" \
    CAIRN_DATA_DIR=/app/data
EXPOSE 8000
CMD ["sh", "-c", "cairn bootstrap; exec uvicorn cairn.api.app:app --host :: --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips '*'"]
