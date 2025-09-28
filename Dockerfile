# syntax=docker/dockerfile:1
FROM python:3.12-slim

# Install uv (pinned) from the official distroless image.
COPY --from=ghcr.io/astral-sh/uv:0.11.28 /uv /uvx /bin/

WORKDIR /app

# Install dependencies first for better layer caching.
COPY pyproject.toml README.md ./
COPY uv.lock ./
COPY src ./src
RUN uv sync --frozen --no-dev

ENV WEBHOOK_RELAY_HOST=0.0.0.0 \
    WEBHOOK_RELAY_PORT=8000 \
    WEBHOOK_RELAY_DB=/data/webhook_relay.db
VOLUME ["/data"]
EXPOSE 8000

CMD ["uv", "run", "--no-dev", "python", "-m", "webhook_relay"]
