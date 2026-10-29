FROM ghcr.io/astral-sh/uv:0.9.21 AS uv
FROM python:3.12-slim
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
# ffmpeg: registering a lab's own recording remuxes it for playback
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" TRANSLUCENT_LIVE=0 TRANSLUCENT_SAM=strict \
    TRANSLUCENT_LEDGER_DIR=/ledger TRANSLUCENT_STATE_DIR=/state \
    TRANSLUCENT_EXPERIMENTS_DIR=/ledger/experiments
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY translucent/ ./translucent/
COPY data/groundtruth/ ./data/groundtruth/
RUN uv sync --frozen --no-dev && mkdir /ledger /state && chown 10001:10001 /ledger /state
USER 10001:10001
VOLUME ["/ledger", "/state"]
EXPOSE 8000
CMD ["translucent", "serve", "--host", "0.0.0.0", "--port", "8000"]
