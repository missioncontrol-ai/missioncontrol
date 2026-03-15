# MissionControl Iteration Board

## Strategy Notes (Formalized)
- Runtime architecture:
  - MCP is the primary integration layer (transport, auth boundary, tool contracts, reliability).
  - Skills are a secondary orchestration layer and should call MCP tools as system of record.
- Product direction:
  - Add mission-scoped and kluster-scoped skill synchronization for connected agents.
  - Sync should load the latest MC source-of-truth skills for active mission/kluster context.
  - Evaluate two execution models:
    - isolated local workspace per mission/kluster
    - ephemeral in-session skill mount during active context
  - Select a path based on reliability, versioning, and rollback safety.


## Operating Rules
- Owner default: `merlin`
- Priority scale: `P0` critical, `P1` high, `P2` normal, `P3` low
- Status set: `Now`, `Next`, `Backlog`, `Blocked`, `Done`
- WIP limit for `Now`: `5`
- Update cadence: daily updates, weekly snapshot refresh
- Definition of done: acceptance criteria met, tests pass, and docs/runbooks updated

## Weekly Snapshot (2026-03-06)
- Top priorities:
  - Deploy-safe feedback triage loop for continuous learning
  - Stabilize private->public `mc-integration` subtree release workflow
  - Harden token-auth testing phase UX and onboarding docs
- Active blockers:
  - None recorded
- Recently completed:
  - UI token login gate shipped for testing phase
  - Onboarding manifest sanitized to placeholder endpoints
  - Slack integration MVP endpoints and workflows delivered

## Next Build Steps (2026-03-15)
1. `MC-PROFILE-002`: finish identity header wiring (agent/session/profile) and validate with concurrent multi-agent launches.
2. `MC-MCP-BOOST-001`: stop booster short-circuit from masking authoritative MCP read/mutation paths by default.
3. `MC-MCP-003`: publish parity matrix (REST vs MCP) and open explicit gaps list.
4. `MC-MCP-004`: close remaining create-flow parity gaps (`create_kluster` + critical creates) with E2E tests.
5. `MC-MCP-005`: standardize MCP response/error envelope and compatibility tests before release gating.

## Post-Cutover Risk Sweep (2026-03-15)
- [ ] `MC-PERSIST-002` GitHub App credential lease flow (server-side JWT -> installation token)
  - Owner: `merlin`
  - Priority: `P0`
  - Status: `Now`
  - Target Date: `2026-03-18`
  - Links: `backend/app/services/persistence_publish.py`, `backend/app/routers/persistence.py`
  - Acceptance Criteria: `github_app` provider mints short-lived installation tokens server-side (no static PAT dependency), validates repo/install permissions, and never returns credential material in API/MCP responses.

- [ ] `MC-PERSIST-003` Publish idempotency + concurrency guardrails
  - Owner: `merlin`
  - Priority: `P0`
  - Status: `Now`
  - Target Date: `2026-03-18`
  - Links: `backend/app/services/persistence_publish.py`, `backend/app/services/git_ledger.py`
  - Acceptance Criteria: repeated publish requests are safe (idempotency key or deterministic dedupe), concurrent publish requests for same mission are serialized or conflict-safe, and ledger/publication states remain consistent.

- [ ] `MC-PERSIST-004` Persistence API + MCP E2E coverage
  - Owner: `merlin`
  - Priority: `P1`
  - Status: `Next`
  - Target Date: `2026-03-19`
  - Links: `backend/tests`, `backend/app/routers/persistence.py`, `backend/app/routers/mcp.py`
  - Acceptance Criteria: tests cover `/persistence` CRUD/plan/execute flows and MCP `provision_mission_persistence|resolve_publish_plan|get_publication_status` with authz and failure scenarios.

- [ ] `MC-DOC-009` Remove stale references to deleted Python MCP paths in tracker/docs
  - Owner: `merlin`
  - Priority: `P1`
  - Status: `Next`
  - Target Date: `2026-03-17`
  - Links: `.dev/TODO.md`, `docs/MC-RUST.md`
  - Acceptance Criteria: no active todo/docs links point to deleted `integrations/missioncontrol-mcp` or `distribution/missioncontrol-mcp` paths.

## Now
- [x] `MC-PROFILE-001` Profile-aware mc launch UX (new vs resume)
  - Owner: `merlin`
  - Priority: `P0`
  - Target Date: `2026-03-17`
  - Links: `integrations/mc/src/launch.rs`, `docs/AGENT-INSTALL.md`, `docs/MC-RUST.md`
  - Acceptance Criteria: `mc launch` supports profile selection and `new|resume` flow; `mc launch <agent>` defaults to new session; runtime/session metadata persisted for resume.
  - Completed: `2026-03-15`
  - Notes: launch session model and resume flow landed in Rust cutover sequence.

- [ ] `MC-PROFILE-002` Profile + session-overlay runtime isolation
  - Owner: `merlin`
  - Priority: `P0`
  - Target Date: `2026-03-18`
  - Links: `integrations/mc/src/launch.rs`, `integrations/mc/src/config.rs`, `integrations/mc/src/client.rs`
  - Acceptance Criteria: runtime launches isolate per-instance state under session-specific directories while keeping profile-level persistence; identity headers include agent/session context.
  - Notes: session/profile overlay isolation is mostly in place; identity header propagation is remaining completion work.

- [x] `MC-PROFILE-003` Central profile sync contract and activation semantics
  - Owner: `merlin`
  - Priority: `P0`
  - Target Date: `2026-03-19`
  - Links: `backend/app/routers/profiles.py`, `backend/app/models.py`, `backend/tests/test_profiles.py`
  - Acceptance Criteria: publish/fetch/activate profile flow documented and validated for cross-machine usage with version and integrity expectations.
  - Completed: `2026-03-15`
  - Notes: MCP profile lifecycle tools + CLI publish/pull/status routing + pin conflict checks + profile CLI tests.

- [x] `MC-PROFILE-004` Runtime cleanup/retention policy + doctor integration
  - Owner: `merlin`
  - Priority: `P0`
  - Target Date: `2026-03-20`
  - Links: `integrations/mc/src/maintenance.rs`, `integrations/mc/src/launch.rs`, `docs/MC-RUST.md`
  - Acceptance Criteria: configurable retention for instances/bundles is available, garbage collection is safe/idempotent, and doctor surfaces cleanup/retention status with clear operator hints.
  - Completed: `2026-03-15`
  - Notes: `mc maintenance doctor --cleanup` with retention knobs for instances/bundles/age.

- [x] `MC-LAUNCH-001` Agent launch regression harness (all supported runtimes)
  - Owner: `merlin`
  - Priority: `P0`
  - Target Date: `2026-03-21`
  - Links: `integrations/mc/src/launch.rs`, `scripts/test.sh`, `scripts/mc-pressure-test.sh`, `docs/MC-RUST.md`
  - Acceptance Criteria: automated smoke suite validates `mc launch` for codex/claude/gemini/openclaw/custom in `preflight` and real config modes; failures capture per-agent diagnostics and block release.
  - Completed: `2026-03-15`
  - Notes: launch regression harness and test gate wiring landed.

- [x] `MC-LAUNCH-002` Enforce and test instance-local config isolation
  - Owner: `merlin`
  - Priority: `P0`
  - Target Date: `2026-03-21`
  - Links: `integrations/mc/src/launch.rs`, `backend/tests`, `docs/AGENT-INSTALL.md`
  - Acceptance Criteria: launch path never mutates global agent config by default; tests assert config writes occur only under session instance home and document `--legacy-global-config` behavior if retained.
  - Completed: `2026-03-15`
  - Notes: instance-local defaults enabled with explicit legacy global-config escape hatch.

- [ ] `MC-MCP-BOOST-001` Fix mc booster short-circuiting authoritative MCP tool calls
  - Owner: `merlin`
  - Priority: `P0`
  - Target Date: `2026-03-16`
  - Links: `integrations/mc`, `backend/app/routers/mcp.py`, `.dev/missioncontrol-doc-catalog-plan.md`
  - Acceptance Criteria: booster never silently replaces source-of-truth mutation/read paths (`list_missions`, `create_mission`, `create_kluster`, `create_doc`) unless explicitly configured; default behavior falls through to real MCP backend; tests cover short-circuit/fall-through behavior

- [ ] `MC-CL-001` Continuous learning triage workflow API
  - Owner: `merlin`
  - Priority: `P0`
  - Target Date: `2026-03-08`
  - Links: `backend/app/routers/feedback.py`, `backend/tests/test_feedback.py`
  - Acceptance Criteria: feedback entries support triage status/priority/owner/disposition; summary and list queries expose triage dimensions

- [ ] `MC-REL-001` Subtree public release workflow operationalized
  - Owner: `merlin`
  - Priority: `P0`
  - Target Date: `2026-03-08`
  - Links: `scripts/release-mc-integration-subtree.sh`, `docs/MC-INTEGRATION-SUBTREE.md`
  - Acceptance Criteria: deterministic dry-run + push workflow documented and repeatable from missioncontrol repo

- [ ] `MC-SEC-001` Testing-phase token security hardening
  - Owner: `merlin`
  - Priority: `P1`
  - Target Date: `2026-03-10`
  - Links: `docs/OIDC.md`, `README.md`
  - Acceptance Criteria: token handling guidance includes rotation cadence, environment handling, and minimum deployment settings

## Next
- [ ] `MC-SK-001` Skills vs MCP capability strategy
  - Owner: `merlin`
  - Priority: `P1`
  - Target Date: `2026-03-14`
  - Links: `README.md`, `.dev/CHECKPOINT-2026-03-06.md`
  - Acceptance Criteria: decision memo defines when to use skills, MCP, or both, with migration path

- [ ] `MC-MCP-002` Add MCP `create_kluster` tool (plan then execute)
  - Owner: `merlin`
  - Priority: `P1`
  - Target Date: `2026-03-14`
  - Links: `backend/app/routers/mcp.py`, `integrations/missioncontrol-mcp/src/missioncontrol_mcp/server.py`, `integrations/missioncontrol-mcp/tests`
  - Acceptance Criteria: MCP tool catalog includes `create_kluster`, tool call creates mission-linked klusters with REST parity for policy/authz, and end-to-end MCP tests pass

- [ ] `MC-SK-002` Mission/Kluster skill sync architecture research
  - Owner: `merlin`
  - Priority: `P1`
  - Target Date: `2026-03-14`
  - Links: `README.md`, `distribution/mc-integration`, `.dev/CHECKPOINT-2026-03-06.md`
  - Acceptance Criteria: research doc compares sync models, defines source-of-truth/versioning strategy, and recommends implementation path with failure handling

- [ ] `MC-CL-002` Weekly feedback review loop
  - Owner: `merlin`
  - Priority: `P1`
  - Target Date: `2026-03-14`
  - Links: `backend/app/routers/feedback.py`
  - Acceptance Criteria: review output identifies accepted/rejected items and linked implementation outcomes

- [ ] `MC-MCP-003` MCP parity audit and gap register
  - Owner: `merlin`
  - Priority: `P0`
  - Target Date: `2026-03-10`
  - Links: `backend/app/routers/mcp.py`, `backend/app/routers/*.py`, `docs/architecture.md`
  - Acceptance Criteria: matrix maps REST critical operations to MCP tools, identifies missing parity, and defines owners/dates for every gap

- [ ] `MC-MCP-004` Add missing MCP create flows (`create_kluster` + critical creates)
  - Owner: `merlin`
  - Priority: `P0`
  - Target Date: `2026-03-11`
  - Links: `backend/app/routers/mcp.py`, `backend/tests`, `integrations/missioncontrol-mcp/tests`
  - Acceptance Criteria: missing create tools implemented with authz/governance parity, documented schemas, and passing MCP E2E tests

- [ ] `MC-MCP-005` Standardize MCP response/error contract
  - Owner: `merlin`
  - Priority: `P1`
  - Target Date: `2026-03-12`
  - Links: `backend/app/routers/mcp.py`, `integrations/missioncontrol-mcp/src/missioncontrol_mcp/server.py`
  - Acceptance Criteria: all tools return consistent envelope/error codes/request_id behavior; legacy responses covered by compatibility tests

- [ ] `MC-MCP-006` Skills bundle security hardening
  - Owner: `merlin`
  - Priority: `P0`
  - Target Date: `2026-03-12`
  - Links: `backend/app/services/skills.py`, `backend/tests/test_skills_sync.py`
  - Acceptance Criteria: tar size/path traversal/bomb protections enforced, integrity metadata validated, and adversarial tests pass

- [ ] `MC-MCP-007` Mutation idempotency and replay safety
  - Owner: `merlin`
  - Priority: `P0`
  - Target Date: `2026-03-13`
  - Links: `backend/app/routers/mcp.py`, `backend/app/services/governance.py`, `backend/tests`
  - Acceptance Criteria: repeated mutation calls are safe/deterministic, replay attempts are detected/rejected where required, and tests cover duplicates

- [ ] `MC-MCP-008` Concurrency integrity for skills snapshot lifecycle
  - Owner: `merlin`
  - Priority: `P1`
  - Target Date: `2026-03-13`
  - Links: `backend/app/services/skills.py`, `backend/app/models.py`, `backend/tests/test_skills_sync.py`
  - Acceptance Criteria: concurrent promote/deprecate/resolve flows remain consistent; constraints and transaction boundaries validated by tests

- [ ] `MC-MCP-009` Source/dist drift elimination for MCP bridge
  - Owner: `merlin`
  - Priority: `P1`
  - Target Date: `2026-03-14`
  - Links: `integrations/missioncontrol-mcp`, `distribution/mc-integration/missioncontrol-mcp`, `scripts/sync-mc-integration-mcp.sh`
  - Acceptance Criteria: canonical source is explicit, sync step is enforced in CI, and no manual mirrored edits are required

- [ ] `MC-MCP-010` MCP observability baseline (SLO metrics + structured logs)
  - Owner: `merlin`
  - Priority: `P1`
  - Target Date: `2026-03-15`
  - Links: `backend/app/main.py`, `backend/app/routers/mcp.py`, `backend/app/services/telemetry.py`
  - Acceptance Criteria: per-tool latency/error metrics emitted with request correlation; baseline dashboards and alert thresholds documented

- [ ] `MC-MCP-011` Doctor and operational diagnostics upgrade
  - Owner: `merlin`
  - Priority: `P1`
  - Target Date: `2026-03-15`
  - Links: `integrations/missioncontrol-mcp/src/missioncontrol_mcp/server.py`, `distribution/mc-integration/scripts/doctor.*`
  - Acceptance Criteria: doctor validates connectivity/auth/tool surface/snapshot endpoints/sync state and returns actionable failure categories

- [ ] `MC-MC-001` Matrix telemetry doc + daemon fan-out
  - Owner: `merlin`
  - Priority: `P1`
  - Target Date: `2026-03-20`
  - Links: `docs/ARCHITECTURE-COMPARISON-RUFLO.md`, `docs/REAL-TIME.md (planned)`, `integrations/mc/src/daemon.rs`
  - Acceptance Criteria: `/events/stream` schema documented, daemon reconnect/backoff behaviors defined, and SSE/WebSocket fan-out story aligned with Ruflo matrix expectations

- [ ] `MC-MC-002` WASM agent booster + MQTT sync plumbing
  - Owner: `merlin`
  - Priority: `P1`
  - Target Date: `2026-03-24`
  - Links: `integrations/mc/src/`, `backend/app/services/schema_pack.py`, `docs/MC-RUST.md`
  - Acceptance Criteria: launcher embeds pluggable WASM hook for fast-path validation, daemon advertises MQTT sync intent, and booster/MQTT stories documented for Ruflo-style swarms

- [ ] `MC-MC-003` CLI parity & docs expansion
  - Owner: `merlin`
  - Priority: `P1`
  - Target Date: `2026-03-26`
  - Links: `integrations/mc/src/commands.rs`, `integrations/mc/README.md`, `docs/MC-RUST.md`
  - Acceptance Criteria: every Python bridge command has Rust parity and tests, README/MC-MCP doc cover onboarding + doctor + matrix, and CLI usage stories updated for local users

- [ ] `MC-MC-004` Operational hardening checklist for mc
  - Owner: `merlin`
  - Priority: `P1`
  - Target Date: `2026-03-28`
  - Links: `docs/ARCHITECTURE-COMPARISON-RUFLO.md`, `docs/MC-RUST.md`, `docker-compose.yml`, `.dev/RELEASE-CHECKLIST.md`
  - Acceptance Criteria: TLS/rate-limit/secret guidance reflects mc daemon, Compose healthchecks/resource limits mention Rust agent, and release checklist references new doctor/telemetry expectations

- [ ] `MC-MCP-012` Security regression suite for MCP critical path
  - Owner: `merlin`
  - Priority: `P0`
  - Target Date: `2026-03-16`
  - Links: `backend/tests/test_security_adversarial.py`, `backend/tests`, `integrations/missioncontrol-mcp/tests`
  - Acceptance Criteria: cross-mission access, policy bypass, approval tamper/replay, and malformed artifact cases are covered and pass in CI

- [ ] `MC-MCP-013` Release gate for MCP top-tier readiness
  - Owner: `merlin`
  - Priority: `P0`
  - Target Date: `2026-03-17`
  - Links: `.github/workflows/*.yml`, `distribution/mc-integration/docs/RELEASE-CHECKLIST.md`
  - Acceptance Criteria: release pipeline blocks on parity/security/doctor/E2E checks and publishes only when all critical gates pass

## Backlog
- [ ] `MC-TEST-003` Storage profile parity tests (Postgres+pgvector and SQLite+Chroma)
  - Owner: `merlin`
  - Priority: `P1`
  - Target Date: `2026-03-22`
  - Links: `backend/tests`, `scripts/test.sh`, `docker-compose.yml`, `docker-compose.quickstart.yml`
  - Acceptance Criteria: automated suite validates equivalent behavior across both storage profiles in CI

- [ ] `MC-TEST-004` End-to-end authenticated CRUD smoke checks for both Docker profiles
  - Owner: `merlin`
  - Priority: `P1`
  - Target Date: `2026-03-23`
  - Links: `scripts/smoke.sh`, `scripts/dev-up.sh`, `scripts/dev-up-full.sh`
  - Acceptance Criteria: smoke checks cover authenticated CRUD and fail fast on profile regressions

- [ ] `MC-MIG-001` Incremental migration discipline for every schema change
  - Owner: `merlin`
  - Priority: `P1`
  - Target Date: `2026-03-24`
  - Links: `backend/alembic`, `.github/workflows/ci-migrations.yml`
  - Acceptance Criteria: schema-changing PRs must include forward migration and CI verifies upgrade path integrity

- [ ] `MC-LEDGER-001` Optional hybrid Git+S3 ledger architecture decision
  - Owner: `merlin`
  - Priority: `P2`
  - Target Date: `2026-03-28`
  - Links: `README.md`, `docs/architecture.md`
  - Acceptance Criteria: decision memo defines retention/reconciliation model and recommends go/no-go path

- [ ] `MC-UX-001` Interactive terminal quick actions over MCP transport
  - Owner: `merlin`
  - Priority: `P2`
  - Target Date: `2026-03-29`
  - Links: `integrations/missioncontrol-mcp`, `distribution/mc-integration`
  - Acceptance Criteria: terminal explorer supports create/update/publish quick actions through MCP-only execution path

- [ ] `MC-CLI-001` `mc` short-form CLI design (`mc kluster view ...`)
  - Owner: `merlin`
  - Priority: `P2`
  - Target Date: `2026-03-21`
  - Links: `integrations/missioncontrol-mcp`, `distribution/mc-integration`
  - Acceptance Criteria: command grammar, auth model, and MVP command set documented

- [ ] `MC-AUTH-002` OIDC onboarding path for colleagues
  - Owner: `merlin`
  - Priority: `P2`
  - Target Date: `2026-03-28`
  - Links: `docs/OIDC.md`
  - Acceptance Criteria: optional OIDC login flow documented for phased rollout

## Blocked
- [ ] None

## Done
- [x] `MC-OSS-001` Public repo governance and brand-protection baseline
  - Completed: `2026-03-06`
  - Notes: added Apache-2.0 license artifacts (`LICENSE`, `NOTICE`), trademark policy, contribution/governance/security/support docs, issue/PR templates, and CI hardening (`dco.yml`, `codeql.yml`, `dependabot.yml`)

- [x] `MC-MCP-001` Codex MCP startup failure fixed for MissionControl integration
  - Completed: `2026-03-06`
  - Notes: installed local `missioncontrol-mcp` with `pipx`, fixed Codex config typo (`startup_timeout_sec`), and pinned absolute MCP command path in `~/.codex/config.toml`; `missioncontrol-mcp doctor` passes against `https://mc.merlinlabs.cloud` with tools discovered

- [x] `MC-UI-001` Token login screen + continue flow functional
  - Completed: `2026-03-06`
  - Notes: users can gate UI access with token for current testing phase

- [x] `MC-UI-002` Agent activation examples sanitized to placeholders
  - Completed: `2026-03-06`
  - Notes: removed homelab-specific endpoint leakage from onboarding payloads

- [x] `MC-INT-001` Slack integration MVP endpoints + tests
  - Completed: `2026-03-06`
  - Notes: secure inbound verification, bindings, command workflows, and interaction callbacks implemented

## Checkpoint References
- `./CHECKPOINT-2026-03-06.md`
- `./DEPLOY-RESET-2026-03-06.md`
- `./TEST-PLAN-MCP-2026-03-06.md`
