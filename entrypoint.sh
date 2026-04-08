#!/bin/sh
# ┌─────────────────────────────────────────────────────────────────┐
# │  FILE PATH:  ./entrypoint.sh                                    │
# │  Placed at:  project root  (same level as Dockerfile)           │
# │  Purpose:    wait for DB → run migrations → start uvicorn       │
# └─────────────────────────────────────────────────────────────────┘
set -e

echo "==> Waiting for database to be ready..."
until python -c "
import asyncio, asyncpg, os, sys
async def check():
    url = os.environ['DATABASE_URL'].replace('+asyncpg', '')
    try:
        conn = await asyncpg.connect(url)
        await conn.close()
    except Exception as e:
        print(f'  DB not ready: {e}', file=sys.stderr)
        sys.exit(1)
asyncio.run(check())
" 2>/dev/null; do
  echo "  ...retrying in 2s"
  sleep 2
done
echo "  DB is up."

echo "==> Running Alembic migrations..."
# alembic.ini is at /app/alembic.ini  (WORKDIR = /app in Dockerfile)
# alembic.ini must have:  script_location = app/alembic
alembic upgrade head
echo "  Migrations done."

echo "==> Starting FastAPI (uvicorn)..."
exec uvicorn app.api.main:app --host 0.0.0.0 --port 8000