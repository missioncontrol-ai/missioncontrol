# MissionControl Agent Install (Codex + Claude)

Goal: make local coding agents talk to MissionControl via MCP.

## 0) One-Command Bootstrap (recommended)

```bash
MC_TOKEN="Change-Now-Socrates-Plato-Aristotle-Aurelius" \
bash <(curl -fsSL https://raw.githubusercontent.com/missioncontrol-ai/missioncontrol/main/scripts/bootstrap-missioncontrol-agent.sh)
```

Or from local clone:

```bash
MC_TOKEN="Change-Now-Socrates-Plato-Aristotle-Aurelius" \
bash scripts/bootstrap-missioncontrol-agent.sh
```

## 0.5) Generate Agent Config Snippets (optional)

Generate machine-readable MCP config snippets from MissionControl's onboarding manifest:

```bash
bash scripts/generate-agent-config.sh --base-url http://localhost:8008 --agent all --out ./generated-agent-config
```

This writes:
- `generated-agent-config/missioncontrol.mcp.json`
- `generated-agent-config/missioncontrol.legacy.mcp.json`
- `generated-agent-config/codex.mcp.json`
- `generated-agent-config/claude-code.mcp.json`
- `generated-agent-config/openclaw.mcp.json`
- `generated-agent-config/nanoclaw.mcp.json`
- `generated-agent-config/codex.mcp.toml`
- `generated-agent-config/codex.legacy.mcp.toml`
- `generated-agent-config/claude.mcp.json`
- `generated-agent-config/openclaw.acp.json`
- `generated-agent-config/nanoclaw.acp.json`

## 1) Install Bridge

```bash
pipx install "git+ssh://git@github.com/missioncontrol-ai/missioncontrol.git#subdirectory=distribution/missioncontrol-mcp"
```

## 2) Set MissionControl Endpoint

Use the Tailscale MagicDNS route (recommended):

```bash
export MC_BASE_URL="https://mc.example.com"
export MC_BASE_URLS="https://mc.example.com,http://localhost:8008"
export MC_TOKEN="Change-Now-Socrates-Plato-Aristotle-Aurelius"
```

Fallback (if you intentionally want public ingress):

```bash
export MC_BASE_URL="https://mc.example.com"
```

## 3) Install mc (one-time per update)

```bash
bash scripts/install-mc.sh
```

By default this installs to `~/.local/bin/mc`. Ensure `~/.local/bin` is on `PATH`.

## 4) Start Rust Daemon (every session)

Run the Rust daemon (canonical control plane):

```bash
MC_BASE_URL="https://mc.example.com" MC_TOKEN="Change-Now-Socrates-Plato-Aristotle-Aurelius" \
bash scripts/start-mc-daemon.sh
```

Equivalent direct command:

```bash
MC_BASE_URL="https://mc.example.com" MC_TOKEN="Change-Now-Socrates-Plato-Aristotle-Aurelius" \
mc daemon --shim-host 127.0.0.1 --shim-port 8765
```

By default, `start-mc-daemon.sh` starts shim API only and disables matrix streaming (`MC_ENABLE_MATRIX=0`).
Enable matrix streaming explicitly when your backend exposes `/events/stream`:

```bash
MC_ENABLE_MATRIX=1 MC_MATRIX_ENDPOINT=/events/stream \
MC_BASE_URL="https://mc.example.com" MC_TOKEN="Change-Now-Socrates-Plato-Aristotle-Aurelius" \
bash scripts/start-mc-daemon.sh
```

## 5) Add MCP Server to Your Client

Default is shim mode (Python bridge only as stdio transport, Rust daemon handles control flow).

Use this MCP server definition:

```json
{
  "missioncontrol": {
    "command": "missioncontrol-mcp",
    "env": {
      "MC_MCP_MODE": "shim",
      "MC_DAEMON_HOST": "127.0.0.1",
      "MC_DAEMON_PORT": "8765",
      "MC_FAIL_OPEN_ON_LIST": "1",
      "MC_BASE_URL": "https://mc.example.com",
      "MC_BASE_URLS": "https://mc.example.com,http://localhost:8008",
      "MC_STARTUP_PREFLIGHT": "none",
      "MC_HTTP_TIMEOUT_SEC": "20",
      "MC_HTTP_RETRIES": "2",
      "MC_HTTP_RETRY_BACKOFF_MS": "250",
      "MC_TOKEN": "Change-Now-Socrates-Plato-Aristotle-Aurelius"
    }
  }
}
```

Codex timeout defaults in `~/.codex/config.toml`:

```toml
[mcp_servers.missioncontrol]
command = "missioncontrol-mcp"
startup_timeout_sec = 45
tool_timeout_sec = 60
env = { MC_MCP_MODE = "shim", MC_DAEMON_HOST = "127.0.0.1", MC_DAEMON_PORT = "8765", MC_FAIL_OPEN_ON_LIST = "1", MC_STARTUP_PREFLIGHT = "none", MC_BASE_URL = "https://mc.example.com", MC_TOKEN = "Change-Now-Socrates-Plato-Aristotle-Aurelius" }
```

Legacy direct mode remains available only for explicit fallback (`missioncontrol.legacy.mcp.json`, `codex.legacy.mcp.toml`).

## 6) Validate In Agent

Ask agent to list tools and call one:

- list tools
- create a task in cluster 1
- list tasks in cluster 1

## 7) Skill Sync (Mission/Kluster Scope)

Resolve and materialize effective skills for an active mission/kluster:

```bash
MC_BASE_URL="http://localhost:8008" MC_TOKEN="..." \
missioncontrol-mcp sync --mission-id <mission-id> --kluster-id <optional-kluster-id>
```

Check last sync state:

```bash
MC_BASE_URL="http://localhost:8008" MC_TOKEN="..." \
missioncontrol-mcp sync-status --mission-id <mission-id> --kluster-id <optional-kluster-id>
```

Generate signed bundle metadata for skill bundle publish:

```bash
MC_SKILLS_SIGNING_SECRET="..." \
missioncontrol-mcp sign-bundle \
  --bundle-file ./skill-bundle.tar.gz \
  --scope-type mission \
  --scope-id <mission-id> \
  --mission-id <mission-id> \
  --signing-key-id v1
```

## Current Auth

- MVP auth mode is dual:
  - User paths can use OIDC bearer JWTs (Authentik).
  - MCP/agent paths can continue using `MC_TOKEN`.
- MCP bridge clients send `Authorization: Bearer $MC_TOKEN`.

## Next Auth Upgrade Path

- Move MCP bridge from static token to Authentik service-account/client-credentials tokens.

## Troubleshooting: Startup Timeout

If Codex shows `MCP startup incomplete (failed: missioncontrol)`:

- Ensure your MCP env vars are `MC_*` (not `MISSIONCONTROL_*`).
- Ensure `mc daemon` is running on `127.0.0.1:8765`.
- Use shim defaults (`MC_MCP_MODE=shim`, `MC_STARTUP_PREFLIGHT=none`) to avoid backend preflight startup stalls.
