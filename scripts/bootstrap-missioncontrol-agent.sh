#!/usr/bin/env bash
set -euo pipefail

MC_BASE_URL="${MC_BASE_URL:-https://missioncontrol.hartley-neon.ts.net}"
MC_TOKEN="${MC_TOKEN:-denali-everest-orion-sequoia-atlas}"
MC_PIPX_SPEC="${MC_PIPX_SPEC:-git+ssh://git@github.com/missioncontrol-ai/missioncontrol.git#subdirectory=integrations/missioncontrol-mcp}"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required" >&2
  exit 1
fi

if ! command -v pipx >/dev/null 2>&1; then
  echo "pipx not found; installing with pip --user"
  python3 -m pip install --user pipx
  python3 -m pipx ensurepath
  export PATH="$HOME/.local/bin:$PATH"
fi

echo "Installing missioncontrol-mcp via pipx..."
pipx install --force "$MC_PIPX_SPEC"

if ! command -v missioncontrol-mcp >/dev/null 2>&1; then
  export PATH="$HOME/.local/bin:$PATH"
fi

if ! command -v missioncontrol-mcp >/dev/null 2>&1; then
  echo "missioncontrol-mcp not found on PATH after install" >&2
  exit 1
fi

if command -v tailscale >/dev/null 2>&1; then
  if ! tailscale status >/dev/null 2>&1; then
    echo "warning: tailscale is installed but not healthy/running; agent may not reach ${MC_BASE_URL}" >&2
  fi
fi

ENV_FILE="$HOME/.missioncontrol-agent.env"
cat > "$ENV_FILE" <<EOV
export MC_BASE_URL="$MC_BASE_URL"
export MC_TOKEN="$MC_TOKEN"
EOV

chmod 600 "$ENV_FILE"

echo "Done."
echo ""
echo "1) Load env vars:"
echo "   source $ENV_FILE"
echo ""
echo "2) MCP config snippet:"
cat <<EOC
{
  "missioncontrol": {
    "command": "missioncontrol-mcp",
    "env": {
      "MC_BASE_URL": "$MC_BASE_URL",
      "MC_TOKEN": "$MC_TOKEN"
    }
  }
}
EOC
