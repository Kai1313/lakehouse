FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

# Copy dependency manifests first for layer caching
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Pre-cache DuckDB httpfs/delta extensions
RUN .venv/bin/python -c "import duckdb; c = duckdb.connect(); c.execute('INSTALL httpfs; INSTALL delta;'); c.close()"

COPY app ./app

EXPOSE 8001

CMD [".venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
