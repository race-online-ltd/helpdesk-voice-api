# ┌─────────────────────────────────────────────────────────────────┐
# │  FILE PATH:  ./Dockerfile                                       │
# │  Placed at:  project root                                       │
# └─────────────────────────────────────────────────────────────────┘
FROM python:3.13-slim

WORKDIR /app

# System deps: asyncpg needs libpq, uv build needs gcc
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv (fast package manager used by this project)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Layer cache: copy lock files first
COPY pyproject.toml uv.lock ./

# Install all dependencies into the venv
RUN uv sync --frozen --no-dev

# Copy the entire project (app/, alembic.ini, alembic/, scripts/)
COPY . .

# Copy and permission the entrypoint (must come AFTER "COPY . .")
# so it overwrites any stale version from the repo if needed
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Put venv on PATH so alembic, uvicorn etc. are found by entrypoint
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

# entrypoint: wait → migrate → serve
ENTRYPOINT ["/entrypoint.sh"]