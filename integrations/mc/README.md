# mc

Rust-native Mission Control CLI, daemon, and matrix bridge.

This binary (previously referred to as mc-mcp-rs) is now the canonical local agent gateway: it talks
to the FastAPI MCP surface, keeps a lightweight agent context so approvals and sync metadata stay
aligned, and exposes the SSE matrix feed that powers the real-time inbox/approval dashboards described
in [`docs/REAL-TIME.md`](../docs/REAL-TIME.md).

## Building & installing

```
cd integrations/mc
cargo fmt && cargo clippy
cargo test
cargo build --release
cp target/release/mc /usr/local/bin/mc
```

Alternatively, install via `cargo install --path integrations/mc` or ship the binary inside your Linux
package of choice.

## Configuration

ENV | meaning | default
----|---------|--------
`MC_BASE_URL` | base URL for Mission Control API | `http://localhost:8008`
`MC_TOKEN` | bearer token for MCP endpoints | unset
`MC_AGENT_ID` | optional agent identity for governance/sync traces | unset
`MC_TIMEOUT_SECS` | outbound timeout for HTTP calls | `10`
`MC_ALLOW_INSECURE` | accept self-signed certs (daemon use) | `false`
`MC_SCHEMA_PACK_FILE` | optional path to a schema pack JSON to help the booster validate payloads | `docs/schema-packs/main.json`
`MC_BOOSTER_WASM` | optional path to a WASM booster module | embedded default
`MC_DISABLE_BOOSTER` | disable the WASM booster even if configured | `false`
`MC_MQTT_TOPIC` | MQTT topic for inbox updates | `missioncontrol/inbox`

All command-line flags mirror these env vars and can be passed explicitly when needed.

## Command surface

```
mc [--base-url URL] [--token TOKEN] [--agent-id ID] [--allow-insecure] \
   [--booster-wasm PATH] [--disable-booster] <command>
```

### Data tools
- `mc data tools list` — enumerates `/mcp/tools`
- `mc data tools call --tool <tool> --payload <json>` — POST `/mcp/call`

### Data sync
- `mc data sync status --mission-id <id> [--kluster-id <id>] [--agent-id <id>]` — GET `/skills/sync/status`
- `mc data sync promote --mission-id <id> --snapshot-id <id> --snapshot-sha256 <hash> [--kluster-id ...]` — POST `/skills/sync/ack`

### Data explorer
- `mc data explorer tree` — mirrors `/explorer/tree`
- `mc data explorer node --node-type <mission|kluster|task> --node-id <id>` — fetches `/explorer/node/{type}/{id}`

### Admin policy
- `mc admin policy active` — `/governance/policy/active`
- `mc admin policy versions [--limit N]`
- `mc admin policy events [--limit N]`
- `mc approvals list --mission-id <id> [--status <status>] [--limit N]`
- `mc approvals create --mission-id <id> --action <action> [--reason <text>] [--request-context '{...}']`
- `mc approvals approve --approval-id <id> [--expires-in-seconds N] [--note <text>]`
- `mc approvals reject --approval-id <id> [--note <text>]`

### Governance automation
- `mc admin governance roles list --mission-id <id> [--limit N]`
- `mc admin governance roles upsert --mission-id <id> --subject <sub> --role <role>`
- `mc admin governance roles remove --mission-id <id> --subject <sub>`
- `mc admin governance policy active`
- `mc admin governance policy versions [--limit N]`
- `mc admin governance policy create-draft --file policy.json [--change-note text]`
- `mc admin governance policy publish --draft-id N [--change-note text]`
- `mc admin governance policy rollback --version N [--change-note text]`
- `mc admin governance events [--limit N]`

### AI-native operations
- `mc ops mission --action start --kluster-id <id> [--workspace-label <label>] [--agent-id <agent>] [--lease-seconds N]`
- `mc ops mission --action heartbeat --lease-id <id>`
- `mc ops mission --action commit --lease-id <id> --change-set '[{...}]' [--validation-mode <mode>]`
- `mc ops mission --action release --lease-id <id> [--reason text]`

### Agent evolve loop
- `mc agent evolve seed --spec <file>` — POST `/evolve/missions`
- `mc agent evolve run --mission <id> [--agent <name>]` — POST `/evolve/missions/{id}/run`
- `mc agent evolve status --mission <id>` — GET `/evolve/missions/{id}/status`

### Compatibility & drift loop
- `mc system compat matrix run [--providers claude,codex] [--mode smoke|full] [--out <path>]` — runs local compatibility checks and emits `compat-report.json` artifacts under `MC_HOME/compat`.
- `mc system compat matrix report-latest` — prints the latest compatibility artifact (`MC_HOME/compat/latest.json`).
- `mc system drift ingest --provider <name> --version <ver> --source-url <url> --summary <text> [--severity compatible|degraded|breaking]` — records `capability-delta.json` under `MC_HOME/drift`.
- `mc system drift triage [--mission <id>] [--provider <name>]` — merges latest compat+drift artifacts into a `policy-decision.json` gate decision.

### Maintenance & backups
- `mc system doctor [--matrix-endpoint /events/stream] [--matrix-sample-seconds 5] [--fix]`
- `mc system backup [--target postgres|rustfs|all] [--reason <note>]`

### Remote control
- `mc agent remote message --agent-id <id> --to-agent-id <id> --content '<payload>' [--message-type <type>]`
- `mc agent remote sessions list --agent-id <id> [--limit N]`
- `mc agent remote sessions start --agent-id <id> --context '<ctx>'`
- `mc agent remote sessions end --agent-id <id> --session-id <id>`

### Self-update
- `mc system update self-update [--manifest-url URL]`

### Session auth
- `mc auth login [--ttl-hours N] [--print-token]` — exchange current credentials for a revocable session token
- `mc auth whoami` — show current identity from server (`/auth/me`)
- `mc auth logout [--local-only]` — revoke current session token and clear local session file

### Doctor & daemon
- `mc system doctor [--matrix-endpoint /events/stream] [--matrix-sample-seconds 5] [--fix]` — runs the health, tools, and matrix checks described in `[docs/REAL-TIME.md](../docs/REAL-TIME.md)` and prints a JSON report; `--fix` ensures local directories + agent_id metadata are available for future runs.
- `mc daemon --matrix-endpoint /events/stream [--fanout-port <port>] [--mqtt-url mqtt://host:1884] [--mqtt-topic missioncontrol/inbox] [--shim-host 127.0.0.1] [--shim-port 8765] [--tools-cache-ttl-sec 60] [--tools-stale-sec 600] [--shim-token <token>]` — keeps an SSE stream alive for the matrix/inbox feed; fan-out and MQTT options replay the telemetry to local dashboards, and the shim API exposes local `/v1/*` control endpoints for MCP shim clients.

## Real-time matrix and swarm integration

The daemon mode connects to `/events/stream` and prints the chunked telemetry that powers the inbox,
approval, and matrix dashboards. When you pair local swarm-style workflows with Mission Control, run the
`mc daemon` process alongside the swarm’s leader so that the governance plane (approvals, policy
enforcement, skill sync metadata) stays in lockstep with the agent planners and vector memory.

Run `mc daemon` with `--fanout-port <port>` to expose a local SSE server on `/events` for dashboards and
local controller processes. The new [docs/REAL-TIME.md](../docs/REAL-TIME.md) describes the `/events/stream` schema,
rate-limit expectations, reconnect/backoff behavior, and how the daemon should honor ticker headers so the
local fan-out does not exhaust the upstream MQ/NATS guardrails.

The WASM booster runs before every `mc data tools call` (unless disabled via `--disable-booster`). It loads the
configured module (`--booster-wasm`) or the embedded default, validates the JSON payload against the schema
pack configured via `MC_SCHEMA_PACK_FILE`, and if the booster agrees, short-circuits the remote call with a
quick success message so handwritten or automated agents can avoid slow LLM loops. Pointing the env var at
`docs/schema-packs/main.json` keeps the local validation consistent with backend expectations.

The daemon also peeks at MQTT (via `--mqtt-url`/`--mqtt-topic`) and republishes those inbox messages onto the
SSE fan-out so local swarms stay synced.

## Shim API compatibility

`mc daemon` now serves shim-compatible local endpoints by default on `127.0.0.1:8765`:

- `POST /v1/initialize`
- `GET /v1/tools`
- `POST /v1/call`
- `GET /v1/health` (plus `/healthz`, `/readyz`, `/livez`)

This lets MCP shim clients use the Rust daemon as their local control plane while keeping Mission Control
API access centralized in `mc`.

If `--shim-token` (or `MC_DAEMON_SHIM_TOKEN`) is set, shim requests must include either:

- `Authorization: Bearer <token>`
- `X-MC-Shim-Token: <token>`

The Rust CLI keeps scratchstate simple: tools use `serde_json` for payloads, sync/promote automates the
skill sync handshake, and the SSE stream ensures users see rapid alignment or approvals without poll
noise.

## Containerized daemon (optional)
Spin up a hardened container that runs `mc daemon` with `MC_HOME` mounted, fan-out ports exposed, and
secrets injected via Compose-managed files. The default experience still runs the native binary, but the
containerized daemon is recommended for production guardrails.
