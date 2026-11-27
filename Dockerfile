# Build stage: resolve the locked dependencies with uv into /app/.venv.
FROM python:3.11-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.13.9 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# Dependencies first, so code changes do not invalidate this layer.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# The vic package is installed into the venv (not editable), so the runtime
# image needs no src/ folder.
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable


# Runtime stage: same Python base (the venv points at its interpreter), no uv.
FROM python:3.11-slim

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 10001 vic \
    && mkdir /data \
    && chown vic:vic /data

WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY app ./app
COPY deploy/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    VIC_DB_PATH=/data/vic.duckdb

USER vic
EXPOSE 8050

# Liveness only (/healthz does not query DuckDB); the first start may run the
# pipeline before gunicorn comes up, hence the long start period.
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8050/healthz', timeout=4)"]

ENTRYPOINT ["docker-entrypoint.sh"]

# ONE worker process with several threads: DuckDB lets only one process open
# the database file, so a second gunicorn worker (= second process) would fail
# with "Could not set lock". Threads share the process; app/data.py serialises
# their queries with a lock and closes the connection after each query.
CMD ["gunicorn", "--workers", "1", "--threads", "4", \
     "--bind", "0.0.0.0:8050", "--access-logfile", "-", "app:server"]
