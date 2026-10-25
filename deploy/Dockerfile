FROM ghcr.io/astral-sh/uv:0.9.11 AS uv

FROM python:3.14-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

RUN groupadd --system --gid 10001 forge \
    && useradd --system --uid 10001 --gid forge --home-dir /app forge

WORKDIR /app
COPY --from=uv /uv /uvx /bin/
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY README.md ./
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./
COPY scripts/check_startup.py scripts/validate_integrity.py scripts/report_validation_jobs.py scripts/benchmark_auth.py ./scripts/
RUN uv sync --frozen --no-dev

USER forge
CMD ["forge-api"]
