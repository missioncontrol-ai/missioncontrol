#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

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

docker compose "${compose_args[@]}" down
