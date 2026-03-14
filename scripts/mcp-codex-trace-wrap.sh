#!/usr/bin/env bash
set -euo pipefail

TRACE_LOG="${MC_MCP_TRACE_LOG:-/tmp/mc_mcp_trace.log}"
CMD_PATH="${MC_MCP_TRACE_COMMAND:-/home/merlin/.local/share/mise/installs/python/3.12.12/bin/missioncontrol-mcp}"

{
  echo "=== $(date -Is) pid=$$ ==="
  echo "argv: $0 $*"
  env | sort | grep -E '^(MC_|MISSIONCONTROL_|PATH=|PYTHONPATH=)'
} >> "$TRACE_LOG"

exec "$CMD_PATH" "$@" 2>>"$TRACE_LOG"
