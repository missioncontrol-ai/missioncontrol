#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${MC_MCP_PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3)"
  elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python)"
  else
    echo "python is required to run missioncontrol-mcp local adapter" >&2
    exit 1
  fi
fi

exec "$PYTHON_BIN" "$ROOT_DIR/distribution/missioncontrol-mcp/src/missioncontrol_mcp/server.py" serve
