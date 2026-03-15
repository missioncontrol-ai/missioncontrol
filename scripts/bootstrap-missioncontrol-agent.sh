#!/usr/bin/env bash
set -euo pipefail

MC_BASE_URL="${MC_BASE_URL:-https://missioncontrol.hartley-neon.ts.net}"
MC_TOKEN="${MC_TOKEN:-TopSecret}"
echo "Installing mc CLI..."
bash "$(dirname "$0")/install-mc.sh"
export PATH="$HOME/.local/bin:$PATH"
if ! command -v mc >/dev/null 2>&1; then
  echo "mc not found on PATH after install" >&2
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
    "command": "mc",
    "args": ["serve"],
    "env": {
      "MC_BASE_URL": "$MC_BASE_URL",
      "MC_TOKEN": "$MC_TOKEN"
    }
  }
}
EOC
