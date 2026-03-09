#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -f .env ]]; then
  cp .env.example .env
fi

docker compose -f docker-compose.quickstart.yml up -d --build
echo "MissionControl API: http://localhost:8008"
