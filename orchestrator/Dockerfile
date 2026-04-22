FROM python:3.12-slim AS builder

# uv 装包速度快
COPY --from=ghcr.io/astral-sh/uv:0.5.4 /uv /usr/local/bin/uv

WORKDIR /app
# VIRTUAL_ENV 让 uv pip install 默认装到 /opt/venv（不传 --python 也行）
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONDONTWRITEBYTECODE=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

# 先建 venv + 装运行依赖（独立 layer，pyproject 不变就走缓存）
COPY pyproject.toml ./
RUN uv venv $VIRTUAL_ENV && \
    uv pip install \
        fastapi 'uvicorn[standard]' pydantic pydantic-settings httpx \
        asyncpg jinja2 structlog yoyo-migrations psycopg2-binary \
        'kubernetes>=31.0' 'jsonschema>=4.0' 'pyyaml>=6.0'

# 装本包（不重装 deps）
COPY src/ ./src/
COPY migrations/ ./migrations/
RUN uv pip install --no-deps .

# ─── runtime ────────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# psycopg2 运行时要 libpq
RUN apt-get update && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/*

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SISYPHUS_HOST=0.0.0.0 \
    SISYPHUS_PORT=8000 \
    SISYPHUS_MIGRATIONS_DIR=/app/migrations

WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /app/migrations /app/migrations

EXPOSE 8000

# 非 root
RUN useradd -m -u 1000 sisyphus
USER sisyphus

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/healthz').status==200 else 1)"

CMD ["uvicorn", "orchestrator.main:app", "--host", "0.0.0.0", "--port", "8000"]
