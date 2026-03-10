# mc

Rust-native Mission Control CLI, daemon, and matrix bridge.

This binary (previously referred to as mc-mcp-rs) is now the canonical local agent gateway: it talks
to the FastAPI MCP surface, keeps a lightweight agent context so approvals and sync metadata stay
aligned, and exposes the SSE matrix feed that powers the real-time inbox/approval dashboards described
in [docs/ARCHITECTURE-COMPARISON-RUFLO.md](../docs/ARCHITECTURE-COMPARISON-RUFLO.md).

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
`MC_BOOSTER_WASM` | optional path to a WASM booster module | embedded default
`MC_DISABLE_BOOSTER` | disable the WASM booster even if configured | `false`
`MC_MQTT_TOPIC` | MQTT topic for inbox updates | `missioncontrol/inbox`

All command-line flags mirror these env vars and can be passed explicitly when needed.

## Command surface

```
mc [--base-url URL] [--token TOKEN] [--agent-id ID] [--allow-insecure] \
   [--booster-wasm PATH] [--disable-booster] <command>
```

### Tools
- `mc tools list` — enumerates `/mcp/tools`
- `mc tools call --tool <tool> --payload <json>` — POST `/mcp/call`

### Sync
- `mc sync status --mission-id <id> [--kluster-id <id>] [--agent-id <id>]` — GET `/skills/sync/status`
- `mc sync promote --mission-id <id> --snapshot-id <id> --snapshot-sha256 <hash> [--kluster-id ...]` — POST `/skills/sync/ack`

### Explorer
- `mc explorer tree` — mirrors `/explorer/tree`
- `mc explorer node --node-type <mission|kluster|task> --node-id <id>` — fetches `/explorer/node/{type}/{id}`

### Governance
- `mc admin active` — `/governance/policy/active`
- `mc admin versions [--limit N]`
- `mc admin events [--limit N]`
- `mc approvals list --mission-id <id> [--status <status>] [--limit N]`
- `mc approvals create --mission-id <id> --action <action> [--reason <text>] [--request-context '{...}']`
- `mc approvals approve --approval-id <id> [--expires-in-seconds N] [--note <text>]`
- `mc approvals reject --approval-id <id> [--note <text>]`

### Doctor & daemon
- `mc doctor [--matrix-endpoint /events/stream] [--matrix-sample-seconds 5] [--repair]` — runs the health, tools, and matrix checks described in `[docs/REAL-TIME.md](../docs/REAL-TIME.md)` and prints a JSON report; `--repair` ensures local directories + agent_id metadata are available for future runs.
- `mc daemon --matrix-endpoint /events/stream [--fanout-port <port>] [--mqtt-url mqtt://host:1884] [--mqtt-topic missioncontrol/inbox]` — keeps an SSE stream alive for the matrix/inbox feed; fan-out and MQTT options replay the telemetry to local dashboards.

## Real-time matrix & Ruflo integration

The daemon mode connects to `/events/stream` and prints the chunked telemetry that powers the inbox,
approval, and matrix dashboards. When you pair local Ruflo-style swarms with Mission Control, run the
`mc daemon` process alongside the swarm’s leader so that the governance plane (approvals, policy
enforcement, skill sync metadata) stays in lockstep with the agent planners and vector memory described
in [docs/ARCHITECTURE-COMPARISON-RUFLO.md](../docs/ARCHITECTURE-COMPARISON-RUFLO.md).

Run `mc daemon` with `--fanout-port <port>` to expose a local SSE server on `/events` for dashboards and
Ruflo queens. The new [docs/REAL-TIME.md](../docs/REAL-TIME.md) describes the `/events/stream` schema,
rate-limit expectations, reconnect/backoff behavior, and how the daemon should honor ticker headers so the
local fan-out does not exhaust the upstream MQ/NATS guardrails.

The WASM booster runs before every `mc tools call` (unless disabled via `--disable-booster`). It loads the
configured module (`--booster-wasm`) or the embedded default, validates the JSON payload, and if the
booster agrees, short-circuits the remote call with a quick success message so handwritten or automated agents
can avoid slow LLM loops. The daemon also peeks at MQTT (via `--mqtt-url`/`--mqtt-topic`) and republishes 
those inbox messages onto the SSE fan-out so local swarms stay synced.

The Rust CLI keeps scratchstate simple: tools use `serde_json` for payloads, sync/promote automates the
skill sync handshake, and the SSE stream ensures users see rapid alignment or approvals without poll
noise.
