#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -f .env ]]; then
  cp .env.example .env
fi

WIPE_DATA="${MC_DEV_WIPE_DATA:-0}"

if [[ "$WIPE_DATA" == "1" ]]; then
  docker compose -f docker-compose.quickstart.yml down --remove-orphans --volumes
else
  docker compose -f docker-compose.quickstart.yml down --remove-orphans
fi
docker compose -f docker-compose.quickstart.yml up -d --build --remove-orphans
echo "MissionControl API: http://localhost:8008"
