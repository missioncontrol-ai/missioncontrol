#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${MC_BASE_URL:-http://localhost:8008}"
TOKEN="${MC_TOKEN:-}"
SHIM_HOST="${MC_DAEMON_HOST:-127.0.0.1}"
SHIM_PORT="${MC_DAEMON_PORT:-8765}"
MATRIX_ENDPOINT="${MC_MATRIX_ENDPOINT:-/events/stream}"
FANOUT_PORT="${MC_FANOUT_PORT:-}"
ENABLE_MATRIX="${MC_ENABLE_MATRIX:-0}"

if ! command -v mc >/dev/null 2>&1; then
  echo "mc binary not found on PATH." >&2
  echo "Install it first:" >&2
  echo "  bash scripts/install-mc.sh" >&2
  exit 127
fi

if [[ -z "$TOKEN" ]]; then
  echo "MC_TOKEN is required" >&2
  exit 2
fi

echo "starting mc daemon at ${SHIM_HOST}:${SHIM_PORT} (base_url=${BASE_URL})"

args=(
  daemon
  --shim-host "$SHIM_HOST"
  --shim-port "$SHIM_PORT"
)
if [[ "$ENABLE_MATRIX" == "1" ]]; then
  args+=(--matrix-endpoint "$MATRIX_ENDPOINT")
else
  args+=(--disable-matrix)
fi
if [[ -n "$FANOUT_PORT" ]]; then
  args+=(--fanout-port "$FANOUT_PORT")
fi

MC_BASE_URL="$BASE_URL" MC_TOKEN="$TOKEN" exec mc "${args[@]}"
