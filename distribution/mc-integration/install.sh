#!/usr/bin/env bash
set -euo pipefail

MCP_PYPI_SPEC="${MCP_PYPI_SPEC:-missioncontrol-mcp}"
MCP_GITHUB_SPEC="${MCP_GITHUB_SPEC:-git+https://github.com/RyanMerlin/mc-integration.git#subdirectory=missioncontrol-mcp}"
DOCS_URL="${DOCS_URL:-https://github.com/RyanMerlin/mc-integration#readme}"
DEFAULT_LOCAL_ENDPOINT="http://localhost:8008"

ENDPOINT=""
TOKEN=""
AGENT="both"
INSTALL_DIR="${HOME}/.missioncontrol"

usage() {
  cat <<USAGE
Usage: bash install.sh [options]

Options:
  --endpoint URL         MissionControl base URL (optional)
  --token TOKEN          MissionControl token (optional)
  --agent VALUE          codex|claude|both (default: both)
  --install-dir DIR      Output directory (default: ~/.missioncontrol)
  -h, --help             Show help
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --endpoint)
      ENDPOINT="${2:-}"
      shift 2
      ;;
    --token)
      TOKEN="${2:-}"
      shift 2
      ;;
    --agent)
      AGENT="${2:-both}"
      shift 2
      ;;
    --install-dir)
      INSTALL_DIR="${2:-$HOME/.missioncontrol}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

case "$AGENT" in
  codex|claude|both) ;;
  *)
    echo "--agent must be codex|claude|both" >&2
    exit 2
    ;;
esac

if [[ -n "$ENDPOINT" && ! "$ENDPOINT" =~ ^https?:// ]]; then
  echo "--endpoint must start with http:// or https://" >&2
  exit 2
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required" >&2
  exit 1
fi

ensure_pipx() {
  if command -v pipx >/dev/null 2>&1; then
    return 0
  fi
  echo "pipx not found; installing via python3 -m pip --user"
  python3 -m pip install --user pipx
  python3 -m pipx ensurepath || true
  export PATH="$HOME/.local/bin:$PATH"
  if ! command -v pipx >/dev/null 2>&1; then
    echo "pipx installation succeeded but pipx is not on PATH. Add ~/.local/bin to PATH and rerun." >&2
    exit 1
  fi
}

install_mcp() {
  echo "Installing missioncontrol-mcp (PyPI first)..."
  if pipx install --force "$MCP_PYPI_SPEC"; then
    echo "Installed from PyPI spec: $MCP_PYPI_SPEC"
    return 0
  fi
  echo "PyPI install failed. Trying GitHub fallback..."
  pipx install --force "$MCP_GITHUB_SPEC"
  echo "Installed from GitHub fallback spec."
}

json_escape() {
  python3 - "$1" <<'PY'
import json
import sys
print(json.dumps(sys.argv[1]))
PY
}

toml_escape() {
  local value="$1"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  value="${value//$'\n'/\\n}"
  value="${value//$'\r'/\\r}"
  value="${value//$'\t'/\\t}"
  printf '"%s"' "$value"
}

write_env_file() {
  local env_file="$HOME/.missioncontrol-agent.env"
  local effective_endpoint="${ENDPOINT:-$DEFAULT_LOCAL_ENDPOINT}"
  mkdir -p "$(dirname "$env_file")"
  cat > "$env_file" <<ENV
export MC_BASE_URL="${effective_endpoint}"
export MC_TOKEN="${TOKEN}"
ENV
  chmod 600 "$env_file" || true
  echo "$env_file"
}

render_templates() {
  local config_dir effective_endpoint endpoint_json token_json endpoint_toml token_toml
  config_dir="$INSTALL_DIR/config"
  effective_endpoint="${ENDPOINT:-$DEFAULT_LOCAL_ENDPOINT}"
  mkdir -p "$config_dir"

  endpoint_json="$(json_escape "$effective_endpoint")"
  token_json="$(json_escape "$TOKEN")"
  endpoint_toml="$(toml_escape "$effective_endpoint")"
  token_toml="$(toml_escape "$TOKEN")"

  if [[ "$AGENT" == "codex" || "$AGENT" == "both" ]]; then
    cat > "$config_dir/codex.mcp.toml" <<TOML
[mcp_servers.missioncontrol]
command = "missioncontrol-mcp"
startup_timeout_sec = 45
tool_timeout_sec = 60
env = { MC_BASE_URL = $endpoint_toml, MC_TOKEN = $token_toml }
TOML
    echo "wrote $config_dir/codex.mcp.toml"
  fi

  if [[ "$AGENT" == "claude" || "$AGENT" == "both" ]]; then
    cat > "$config_dir/claude.mcp.json" <<JSON
{
  "mcpServers": {
    "missioncontrol": {
      "command": "missioncontrol-mcp",
      "env": {
        "MC_BASE_URL": $endpoint_json,
        "MC_TOKEN": $token_json
      }
    }
  }
}
JSON
    echo "wrote $config_dir/claude.mcp.json"
  fi
}

run_doctor() {
  local script_dir doctor effective_endpoint
  script_dir=""
  if [[ -n "${BASH_SOURCE[0]:-}" && "${BASH_SOURCE[0]}" != "bash" ]]; then
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  fi
  doctor="${script_dir:+$script_dir/scripts/doctor.sh}"
  effective_endpoint="${ENDPOINT:-$DEFAULT_LOCAL_ENDPOINT}"
  if [[ -n "$doctor" && -f "$doctor" ]]; then
    if [[ ! -x "$doctor" ]]; then
      chmod +x "$doctor" || true
    fi
    "$doctor" --endpoint "$effective_endpoint" --token "$TOKEN" || true
    return 0
  fi
  if missioncontrol-mcp --help >/dev/null 2>&1; then
    echo "[OK] missioncontrol-mcp --help"
  else
    echo "[WARN] missioncontrol-mcp exists but --help failed"
  fi
}

print_next_steps() {
  local env_file="$1"
  local config_dir="$INSTALL_DIR/config"
  local doctor_hint
  if [[ -n "${BASH_SOURCE[0]:-}" && "${BASH_SOURCE[0]}" != "bash" ]]; then
    doctor_hint="bash \"$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/scripts/doctor.sh\""
  else
    doctor_hint="missioncontrol-mcp --help"
  fi

  cat <<NEXT

Installation complete.

Next steps:
1) Load environment:
   source "$env_file"

2) Run doctor manually:
   $doctor_hint

3) Add MCP config in your agent:
   - Codex:  $config_dir/codex.mcp.toml
   - Claude: $config_dir/claude.mcp.json

Auth/connect guidance:
- Default endpoint is localhost ($DEFAULT_LOCAL_ENDPOINT).
- To use hosted MissionControl, update MC_BASE_URL and MC_TOKEN in $env_file.
- Docs: $DOCS_URL
NEXT
}

main() {
  ensure_pipx
  install_mcp
  if ! command -v missioncontrol-mcp >/dev/null 2>&1; then
    export PATH="$HOME/.local/bin:$PATH"
  fi
  if ! command -v missioncontrol-mcp >/dev/null 2>&1; then
    echo "missioncontrol-mcp is not on PATH after installation" >&2
    exit 1
  fi

  local env_file
  env_file="$(write_env_file)"
  render_templates
  run_doctor
  print_next_steps "$env_file"
}

main
