# missioncontrol-mcp

MCP stdio bridge so Codex/Claude can use MissionControl tools as first-class MCP tools.

## What It Does

- Exposes `tools/list` and `tools/call` over MCP stdio.
- Bridges those calls to MissionControl API:
  - `GET /mcp/tools`
  - `POST /mcp/call`
- Includes `missioncontrol-explorer` terminal helper for mission/cluster/task tree browsing.

## Install (pipx recommended)

```bash
pipx install /path/to/missioncontrol/distribution/missioncontrol-mcp
```

Or from git:

```bash
pipx install "git+ssh://git@github.com/missioncontrol-ai/missioncontrol.git#subdirectory=distribution/missioncontrol-mcp"
```

## Env Vars

- `MC_BASE_URL` (default: `http://localhost:8008`)
- `MC_BASE_URLS` (optional comma-separated fallback URLs)
- `MC_MCP_MODE` (`direct|shim|daemon`, default: `direct`)
- `MC_TOKEN` (static bearer token; optional when OIDC client credentials are configured)
- `MC_AUTH_MODE` (`auto|token|oidc`, default: `auto`)
- `MC_OIDC_TOKEN_URL` (required for `oidc` mode)
- `MC_OIDC_CLIENT_ID` (required for `oidc` mode)
- `MC_OIDC_CLIENT_SECRET` (required for `oidc` mode)
- `MC_OIDC_AUDIENCE` (optional)
- `MC_OIDC_SCOPE` (optional)
- `MC_OIDC_REFRESH_SKEW_SEC` (default: `120`)
- `MC_STARTUP_PREFLIGHT` (`none|health|tools`, default: `health`)
- `MC_HTTP_TIMEOUT_SEC` (default: `20`)
- `MC_HTTP_RETRIES` (default: `2`)
- `MC_HTTP_RETRY_BACKOFF_MS` (default: `250`)
- `MC_FAIL_OPEN_ON_LIST` (default: `false`)
- `MC_TOOLS_CACHE_TTL_SEC` (default: `60`, daemon mode)
- `MC_TOOLS_STALE_SEC` (default: `600`, daemon mode)
- `MC_DAEMON_HOST` (default: `127.0.0.1`)
- `MC_DAEMON_PORT` (default: `8765`)
- `MC_DAEMON_CONNECT_TIMEOUT_MS` (default: `100`, shim mode)
- `MC_DAEMON_TOOLS_TIMEOUT_MS` (default: `2000`, shim mode)
- `MC_DAEMON_CALL_TIMEOUT_MS` (default: `30000`, shim mode)
- `MC_AGENT_ID` (required to subscribe to MQTT inbox)
- `MQTT_HOST` (required for MQTT inbox)
- `MQTT_PORT` (default: `1883`)
- `MQTT_USERNAME` (default: `missioncontrol`)
- `MQTT_PASSWORD` (required for MQTT inbox)
- `MC_HOME` (default: `~/.missioncontrol`) — local state directory
- `MC_PROFILE` (optional) — active profile name override
- `MC_PROFILE_SYNC_ON_INIT` (default: `true`) — auto-sync profile on MCP initialize
- `MC_PROFILE_SYNC_TIMEOUT_SEC` (default: `10`) — profile sync timeout on init

## Quick Local Test

```bash
MC_BASE_URLS="https://mc.example.com,http://localhost:8008" \
MC_TOKEN="..." \
missioncontrol-mcp
```

Doctor command:

```bash
MC_BASE_URLS="https://mc.example.com" \
MC_TOKEN="..." \
missioncontrol-mcp doctor
```

Always-on daemon mode:

```bash
MC_MCP_MODE=daemon missioncontrol-mcp daemon
```

Shim mode for MCP clients (connects to local daemon on `MC_DAEMON_HOST:MC_DAEMON_PORT`, default `127.0.0.1:8765`):

Preferred local daemon backend (Rust `mc` integration):

```bash
mc daemon --shim-host 127.0.0.1 --shim-port 8765
```

Then run shim mode:

```bash
MC_MCP_MODE=shim missioncontrol-mcp
```

Skill sync helper examples:

```bash
MC_BASE_URL="http://localhost:8008" MC_TOKEN="..." \
missioncontrol-mcp sync --mission-id <mission-id> --kluster-id <optional-kluster-id>
```

```bash
MC_BASE_URL="http://localhost:8008" MC_TOKEN="..." \
missioncontrol-mcp sync-status --mission-id <mission-id> --kluster-id <optional-kluster-id>
```

```bash
MC_BASE_URL="http://localhost:8008" MC_TOKEN="..." \
missioncontrol-mcp sync-promote --mission-id <mission-id> --kluster-id <optional-kluster-id> --note "promote overlay"
```

Skill bundle signing helper (for contributor/admin upload workflows):

```bash
MC_SKILLS_SIGNING_SECRET="..." \
missioncontrol-mcp sign-bundle \
  --bundle-file ./skill-bundle.tar.gz \
  --scope-type mission \
  --scope-id <mission-id> \
  --mission-id <mission-id> \
  --signing-key-id v1
```

Use the output fields `signature_alg`, `signing_key_id`, and `signature` in bundle publish payloads.

Explorer CLI examples:

```bash
MC_BASE_URL="http://localhost:8008" MC_TOKEN="..." missioncontrol-explorer tree --format ansi
```

```bash
MC_BASE_URL="http://localhost:8008" MC_TOKEN="..." missioncontrol-explorer show cluster <cluster-hash>
```

Governance admin examples:

```bash
MC_BASE_URL="http://localhost:8008" MC_TOKEN="..." missioncontrol-explorer admin policy show
```

```bash
MC_BASE_URL="http://localhost:8008" MC_TOKEN="..." missioncontrol-explorer admin policy draft-create --note "harden policy"
```

## Profile Management

Profiles are personal bundles of agent config files (env files, instructions, etc.) stored on the MissionControl backend and synced to `~/.missioncontrol/profiles/active` on the local machine.

```bash
# List available profiles
missioncontrol-mcp profile list

# Switch active profile
missioncontrol-mcp profile use <name>

# Show current active profile contents
missioncontrol-mcp profile show

# Sync active profile from backend
missioncontrol-mcp profile sync

# Create a new empty profile
missioncontrol-mcp profile create <name>

# Push local files into a profile bundle
missioncontrol-mcp profile push <name> [--files file1 file2 ...]

# Pull a profile bundle to a local directory
missioncontrol-mcp profile pull <name> [--out-dir DIR]

# Delete a profile
missioncontrol-mcp profile delete <name>

# Set up shims in ~/.missioncontrol/profiles/active for use in agent env
missioncontrol-mcp profile setup-shims
```

Auto-sync on MCP initialize: when `MC_PROFILE_SYNC_ON_INIT=true` (default), the bridge syncs the active profile in a background thread (bounded by `MC_PROFILE_SYNC_TIMEOUT_SEC`) during MCP startup. This ensures agent files are up to date without blocking tool calls.

MCP tools: `list_profiles`, `switch_profile`, `sync_profile` (handled locally, not forwarded to backend).

## MCP Client Config Snippet

Use this server command in your MCP client config:

```json
{
  "missioncontrol": {
    "command": "missioncontrol-mcp",
    "env": {
      "MC_BASE_URL": "https://mc.example.com",
      "MC_TOKEN": "Change-Now-Socrates-Plato-Aristotle-Aurelius",
      "MC_AGENT_ID": "1",
      "MQTT_HOST": "mosquitto.missioncontrol.svc.cluster.local",
      "MQTT_PORT": "1883",
      "MQTT_USERNAME": "missioncontrol",
      "MQTT_PASSWORD": "change-me"
    }
  }
}
```

## Notes

- This bridge caches synced snapshots/overlays under `~/.missioncontrol/skills` for offline fallback.
- It is safe for multiple local clients to run independently.
- For team agents, prefer a private/internal route over public ingress where available.
