#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -f .env ]]; then
  cp .env.example .env
fi

WIPE_DATA="${MC_DEV_WIPE_DATA:-0}"
STACK_PROFILE="${MC_STACK_PROFILE:-full}"

compose_args=()
case "$STACK_PROFILE" in
  full)
    compose_args=()
    ;;
  quickstart)
    compose_args=(-f docker-compose.quickstart.yml)
    ;;
  *)
    echo "Invalid MC_STACK_PROFILE=${STACK_PROFILE} (expected: full|quickstart)" >&2
    exit 2
    ;;
esac

if [[ "$WIPE_DATA" == "1" ]]; then
  docker compose "${compose_args[@]}" down --remove-orphans --volumes
else
  docker compose "${compose_args[@]}" down --remove-orphans
fi
docker compose "${compose_args[@]}" up -d --build --remove-orphans
echo "MissionControl API: http://localhost:8008 (profile=${STACK_PROFILE})"
