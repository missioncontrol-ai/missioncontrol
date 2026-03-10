# Mission Control Rust CLI (mc)

The `mc` binary replaces the legacy Python bridge with a Rust-first local agent gateway. It carries the agent context, approval hints, and SSE matrix plumbing while keeping the CLI surface small and aligned with the Mission Control API described in [ARCHITECTURE-COMPARISON-RUFLO.md](ARCHITECTURE-COMPARISON-RUFLO.md). Use `mc` for everything local, and treat the Python bridge as a compatibility shim while this crate matures.

## Configuration & expectations

| Env | Meaning | Default |
| --- | --- | --- |
| `MC_BASE_URL` | Mission Control HTTP base URL | `http://localhost:8008` |
| `MC_TOKEN` | MCP bearer token | unset |
| `MC_AGENT_ID` | Optional agent identifier (persisted from `mc doctor --repair`) | unset |
| `MC_TIMEOUT_SECS` | HTTP timeout | `10` |
| `MC_ALLOW_INSECURE` | Accept self-signed certs (used by daemon and doctor matrix checks) | `false` |
| `MC_BOOSTER_WASM` | Path to a WASM booster module | embedded default |
| `MC_DISABLE_BOOSTER` | Skip the booster even if configured | `false` |

The CLI automatically looks for `~/.missioncontrol/agent_id` when no `MC_AGENT_ID` is set. Running `mc doctor --repair` seeds that file and surfaces a JSON report detailing the health/tools/matrix probes explained in [REAL-TIME.md](REAL-TIME.md).

## Command surface

### Tools & governance
- `mc tools list` / `mc tools call --tool x --payload '{}'` directly map to `/mcp/tools` and `/mcp/call`.
- `mc explorer tree|node`, `mc admin`, and `mc approvals` mirror the FastAPI routes as in the Python bridge.
- `mc workspace` command group is unchanged and still handles leases, artifacts, and commits.

### Sync & approvals
- `mc sync status|promote` retains parity with the Python flow, including payload shapes for drift metadata.
- Approval commands (`list`, `create`, `approve`, `reject`) carry the same arguments and headers, stepping over the governance hooks the backend expects.

### Hot paths (doctor + daemon)
- `mc doctor [--matrix-endpoint /events/stream] [--matrix-sample-seconds 5] [--repair]` builds a structured report that checks `/mcp/health`, `/mcp/tools`, and the matrix SSE feed. Failures include curated repair hints, and `--repair` ensures `MC_HOME`/`MC_SKILLS_HOME` exist while persisting an `agent_id` so subsequent runs share a stable identity.
- `mc daemon --matrix-endpoint /events/stream [--fanout-port <port>] [--mqtt-url mqtt://host:1884] [--mqtt-topic missioncontrol/inbox]` keeps an SSE stream alive, replays MQTT inbox messages if configured, and offers WebSocket/SSE fan-out for local dashboards and Ruflo queens.

## Matrix telemetry & Ruflo continuity

The daemon publishes every event described in [REAL-TIME.md](REAL-TIME.md). For Ruflo-style swarms, run `mc daemon` on the leader host and point local dashboards or Queen processes at `http://localhost:<fanout-port>/events`. The doc outlines the JSON schema, rate limits, and the expectation that the daemon respects `remaining/reset_at` before broadcasting events to local SSE/WebSocket listeners.

## WASM booster & doctor alignment

`mc` embeds a WASM booster that runs before every MCP tool call unless `--disable-booster` is set. The default module simply verifies non-empty payloads, but you can drop in a custom Wasm that implements `validate(ptr, len)` for schema gating or fast success paths. The booster still emits telemetry so dashboards see the same matrix data even when the remote call is skipped.

Running `mc doctor` keeps a locked loop on `/events/stream` and captures rate-limit headers, TLS failures, and timeout issues. The JSON report surfaces these diagnostics to ensure the local UX stays “exceptional,” and the same report can be parsed by other packages (Ruflo/OpenClaw) before they bootstrap their swarms.

## Operational notes

- `mc doctor --repair` is the first step in hardened local deployments: it caches agent metadata, confirms directories, and echoes diagnostics to help you rotate tokens or fix TLS issues before burning LLM budget.
- `mc daemon` is now the canonical hot path for approvals, governance alerts, and matrix telemetry. New packages should call `mc daemon` (or embed its fan-out SSE endpoint) so they share the same governance plane that Mission Control enforces.
- Document any additional local hooks in this guide so auditors know how Ruflo swarms, Matrix telemetry, and the WASM booster align with Mission Control’s policy surfaces.
