# MC / Rust CLI

The `mc` binary (previously known as the `missioncontrol-mcp` Python bridge) is the canonical Rust-native local agent gateway.
It carries the agent context, governance headers, and SSE matrix telemetry in a single binary that can ship to laptops, swarm-style
queen hosts, or local planners that need an exceptional offline/online experience. `mc` speaks the same FastAPI surface as the
 Python bridge (tools, explorer, governance, sync) while adding the matrix/doctor/booster hardening documented in
 [`docs/REAL-TIME.md`](REAL-TIME.md).

## Why Rust-first

- All MCP tooling (tools/explorer/admin/approvals) lives inside a single Rust binary so we can bundle TLS, telemetry, and WASM
  booster hooks without spawning Python subprocesses on every call.
- The compiled binary model improves enterprise operability: deterministic deploy artifacts, lower runtime dependency drift,
  and easier endpoint hardening for security teams.
- The daemon command keeps a live `/events/stream` connection, features reconnection/backoff, and fans the stream out via
  SSE/WebSocket so swarm-style teams observe approvals/inbox events without polling.
- The doctor command (built into the same binary) now doubles as the self-repair and diagnostics entry point for teams that
  need hardened local deployments.

## Enterprise Security Posture

- `mc` keeps auth/session handling, policy headers, and MCP transport in one audited binary path.
- MissionControl enforces authorization server-side; agents request actions, they do not own control-plane credentials.
- Profile/session isolation supports multi-agent and multi-session operation on shared hosts without global config collisions.
- Publish provenance (ledger + publication records) provides change traceability for security and compliance reviews.

## Running the CLI

1. Configure your environment:
   1. `export MC_BASE_URL=http://localhost:8008`
   2. `export MC_TOKEN=<missioncontrol token>`
   3. (Optional) `export MC_AGENT_ID=<agent identifier for approval traces>`
   4. (Optional) `export MC_ALLOW_INSECURE=true` when targeting a dev proxy or self-signed TLS endpoint.
2. Build/install the binary: `cargo build --release && cp target/release/mc /usr/local/bin/mc` or `cargo install --path integrations/mc`.
3. Use the command surface described below for tools, sync, governance, and approvals.
4. Run `mc daemon --matrix-endpoint /events/stream` alongside your agents so the governance plane and matrix telemetry stay
   aligned with upstream Mission Control (pass `--mqtt-url` when you already have MQTT inbox wiring).

## Configuration & expectations

| Env | Meaning | Default |
| --- | --- | --- |
| `MC_BASE_URL` | Mission Control HTTP base URL | `http://localhost:8008` |
| `MC_TOKEN` | MCP bearer token | unset |
| `MC_AGENT_ID` | Optional agent identifier (persisted by `mc system doctor --fix`) | unset |
| `MC_TIMEOUT_SECS` | Outbound timeout for HTTP/SSE calls | `10` |
| `MC_ALLOW_INSECURE` | Accept self-signed certs (daemon and doctor matrix checks) | `false` |
| `MC_BOOSTER_WASM` | Path to a custom WASM booster module | embedded default |
| `MC_DISABLE_BOOSTER` | Skip the booster hook even if configured | `false` |
| `MC_ALLOW_BOOSTER_SHORT_CIRCUIT` | Allow booster to short-circuit MCP tool execution | `false` |
| `MC_SCHEMA_PACK_FILE` | Schema pack JSON used to gate `mc data tools call` payloads | `docs/schema-packs/main.json` |

Point `MC_SCHEMA_PACK_FILE` at the same `docs/schema-packs/main.json` that the backend uses so the CLI-level booster, matrix doctor,
and fan-out diagnostics share the same entity expectations. Custom schema packs can live beside your deployment manifests, and the
daemon will warn and fall back to defaults if the JSON is invalid.

When no `MC_AGENT_ID` is provided, `mc` looks for `~/.missioncontrol/agent_id` and `mc system doctor --fix` will seed it and ensure
`MC_HOME`/`MC_SKILLS_HOME` exist so local swarms keep a stable identity.

## Command surface

### Launch isolation
- `mc launch <agent>` now writes agent config into session-local runtime paths under `~/.missioncontrol/instances/<runtime_session_id>/...` by default.
- Use `mc launch --legacy-global-config <agent>` only for compatibility scenarios that still require mutating global files such as `~/.codex/config.toml`.

### Data + admin surface
- `mc data tools list` / `mc data tools call --tool <tool> --payload '{...}'` map directly to `/mcp/tools` and `/mcp/call`.
- `mc data explorer tree|node`, `mc admin policy active|versions|events`, and `mc approvals list/create/approve/reject` mirror the FastAPI
  governance surface that Mission Control exposes.
- `mc workspace load/heartbeat/fetch-artifact/commit/release` keeps parity with the Python bridge, carrying lease IDs, artifacts, and
  change sets unchanged.
- `mc use --kluster-id <id>` is lease-backed and calls `load_kluster_workspace`; it is not a local-only context setter.
- `mc use --release` and `mc release` call `release_kluster_workspace` to close the active lease.
- When switching klusters via `mc use --kluster-id <new>`, `mc` prompts before releasing the previous lease unless `--auto-release` or `-y` is set.
- `mc status --verify-lease` validates the tracked active lease with `heartbeat_workspace_lease`.
- `mc status` reports attached workspace metadata from the lease-backed state cache (`~/.missioncontrol/active_workspace.json`).

### Local-only utilities
- `mc config`, `mc logs`, and `mc completion` are local utility commands.
- These commands do not mutate MissionControl state and are intentionally outside governance policy mutation paths.

### Sync & approvals
- `mc data sync status|promote` retains the existing payload contracts for skill sync and drift metadata so OpenClaw and similar runtimes can honor
  the same ledger expectations.
- Approval commands carry the same `mission_id`, `action`, `request_context`, and decision parameters as before.

### Hot paths (doctor + daemon)
- `mc system doctor [--matrix-endpoint /events/stream] [--matrix-sample-seconds 5] [--fix]` probes `/mcp/health`, `/mcp/tools`, and the
  matrix SSE feed discussed in [`docs/REAL-TIME.md`](REAL-TIME.md), emitting a structured JSON report with repair hints.
  `--fix` ensures local directories exist and persists an `agent_id` so future runs keep the same identity.
- `mc daemon --matrix-endpoint /events/stream [--fanout-port <port>] [--mqtt-url mqtt://host:1884] [--mqtt-topic missioncontrol/inbox]`
  keeps the SSE stream alive, fans it out to localhost, and can replay MQTT inbox updates so local controller processes see one
  unified stream.

## Matrix telemetry and swarm continuity

The daemon publishes every event described in [`docs/REAL-TIME.md`](REAL-TIME.md) (the `type`, `mission_id`, `kluster_id`, `agent_id`,
`status`, `payload`, and rate-limit metadata). Swarm-style teams should run `mc daemon` on their planner host and point dashboards
at `http://localhost:<fanout-port>/events`. The doc also explains reconnect/backoff, SSE fan-out rate-limiting, and MQTT relay
expectations so both matrix telemetry and local governance stay in sync.

## WASM booster & doctor alignment

`mc` embeds a WASM booster that runs before every MCP tool call when enabled (`--booster-wasm` or `MC_BOOSTER_WASM`). The default
module simply asserts that payloads are non-empty, but you can drop in custom Wasm that implements `validate(ptr, len)` for schema
gating or quick success paths while still emitting matrix telemetry. `mc system doctor` keeps a wire on `/events/stream` and records rate-limit
headers, TLS failures, and timeouts so dashboards know whether the daemon is healthy.

## Operational notes

- `mc system doctor --fix` is the recommended first step for hardened deployments: it caches agent metadata, checks directories, and prints
  diagnostics before any agent starts consuming LLMs.
- `mc daemon` is now the canonical hot path for approvals, governance alerts, and matrix telemetry. Other local packages should call
  `mc daemon` (or connect to its fan-out SSE feed) to share the same governance plane that Mission Control enforces.
- Capture additional local hooks (matrix schema consumers, booster wires, MQTT sync intents) here so auditors or local controllers understand how
  they integrate with Mission Control’s policy controls.

## Operational hardening

Follow the Rust CLI production checklist whenever you turn up `mc` for production-grade workloads: terminate TLS through a reverse
proxy, enforce rate limits when you forward `/events/stream`, keep `MC_TOKEN`/OIDC credentials rotated, and expose `/mcp/health`
together with the matrix stream so orchestrators can probe the daemon. Harden daemon hosts by running inside containers so secrets,
fan-out ports, and local storage are scoped per host.

## Web UI

The SvelteKit front-end lives in `web/` (see [`web/README.md`](../web/README.md) for full details). Build it with `npm run build` so
the backend can mount the generated `web/build` assets at `/ui/`, or run `npm run dev` to let the Svelte dev server host it on port 5173
during development. The interface now surfaces the matrix telemetry, explorer tree, onboarding manifest builder, and governance tabs,
all while reusing the same SSE contracts documented in [`docs/REAL-TIME.md`](REAL-TIME.md) plus the telemetry/doctor story.

## Next steps

- Document the SSE schema in [`docs/REAL-TIME.md`](REAL-TIME.md) so `mc daemon` can be wired into dashboards.
- Add formal WASM booster plumbing in `integrations/mc` that mirrors the schema pack validations in `backend/app/services/schema_pack.py`
  so quick checks run before every MCP call.
- Keep expanding the `mc` CLI until every critical Python command has parity and the Rust daemon hosts the matrix + MQTT pipeline for
  teams who prefer Rust-first tooling.

## Build status checkpoint (2026-03-15)

Completed recently:
- `MC-PROFILE-001`: profile-aware launch + new/resume session flow with persisted runtime session metadata.
- `MC-PROFILE-003`: MCP-backed profile lifecycle (`publish`, `pull`, `status`, pin conflict checks) with CLI tests.
- `MC-PROFILE-004`: cleanup/retention integrated with doctor via `mc system doctor --cleanup`.
- `MC-LAUNCH-001`: launch regression harness landed for supported agent runtimes.
- `MC-LAUNCH-002`: instance-local config default with explicit `--legacy-global-config` compatibility mode.

Current next build sequence:
1. `MC-PROFILE-002`: finish agent/session/profile identity propagation and validate concurrent multi-agent launches.
2. `MC-MCP-BOOST-001`: default booster behavior must fall through to authoritative MCP backend paths.
3. `MC-MCP-003`: publish REST vs MCP parity matrix and track remaining gaps (see [`docs/MCP-PARITY-MATRIX.md`](MCP-PARITY-MATRIX.md)).
4. `MC-MCP-004`: close remaining create-flow parity gaps (`create_kluster` + critical creates) with E2E coverage.
5. `MC-MCP-005`: standardize MCP response/error envelope and compatibility assertions before release gating.

Open launch parity gap to close:
- `MC-LAUNCH-CODEX-AUTH-001`: `mc launch claude` now supports working auth pass-through/profile seeding, but Codex launch/auth behavior is not yet equivalently standardized. Either align Codex to the same pass-through + profile-overlay model or enforce a shared MCP-standard launch/auth layer across both agents.

Post-cutover follow-ups:
- `MC-PERSIST-002`: implement full GitHub App installation-token lease flow (server-side) for mission-scoped publish providers.
- `MC-PERSIST-003`: add publish idempotency/concurrency guardrails for mission-scoped routed publishes.
- `MC-PERSIST-004`: extend API/MCP E2E coverage for persistence provisioning, route planning, and publication status/error paths.
