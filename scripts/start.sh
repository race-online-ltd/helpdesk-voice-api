#!/bin/sh
# ┌─────────────────────────────────────────────────────────────────┐
# │  FILE PATH:  ./scripts/start.sh                                 │
# │  Placed at:  scripts/  (next to existing scripts/test.sh)       │
# │  Purpose:    local dev shortcut — mirrors what CI does on VM    │
# └─────────────────────────────────────────────────────────────────┘
set -e

# Copy .env.example to .env if it doesn't exist
if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example — fill in real values before continuing."
  exit 1
fi

export IMAGE_NAME="auto-ticket-classifier"

docker compose up --build -d
echo "Stack is up. Tailing backend logs (Ctrl-C to stop):"
docker logs -f atc-backend