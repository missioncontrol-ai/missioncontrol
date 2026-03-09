# MissionControl Enterprise 90-Day Plan

Date: March 6, 2026
Horizon: 90 days
Priority: Governance and compliance first, with adoption-focused UX
Slack scope: Core ChatOps MVP

## Summary

MissionControl has strong foundations (OIDC/token auth, mission roles, governance policy drafts, MCP, and ledger events), but there are clear philosophy-to-product gaps in Slack workflows, mission profiles, approval trust model, and enterprise operations hardening.

Current gaps identified in the codebase:

- Slack is ingestion-only and currently stubbed, not a bidirectional organizational interface.
- Governance defaults are permissive for production.
- Approval context is header-based and not cryptographically verifiable.
- Search and some read flows are not mission-authorized, creating cross-mission data exposure risk.
- Platform defaults like wildcard CORS are not enterprise-safe.

## 90-Day Implementation Plan

### Phase 0 (Weeks 1-2): Security and Data Boundary Hardening

- Enforce mission-scoped authorization on all read and search paths in REST and MCP.
- Move production defaults to conservative governance:
  - `require_approval_for_mutations=true`
  - `allow_create_without_approval=false`
  - mutation tools disabled unless explicitly enabled by policy
- Replace trust-weak `x-approval-context` flow with signed, short-lived approval tokens (issuer, approver subject, nonce, expiry, request linkage).
- Replace wildcard CORS with environment-based allowlist.
- Set production auth posture to OIDC-first (`AUTH_MODE=oidc`).

### Phase 1 (Weeks 2-5): Slack ChatOps MVP (Adoption-First, Policy-Conservative)

- Implement Slack App integration endpoints with request signature verification.
- Deliver core workflows:
  - task creation from Slack threads
  - in-channel overlap warnings
  - artifact publish notifications
  - governance approval requests and approve/reject actions
  - mission-scoped search from Slack
- Add channel-to-mission bindings to enforce scoped operations.
- Enforce role-aware Slack actions (viewer/contributor/owner/admin) with deny-by-default policy checks.
- Build simple, guided UX in Slack to keep onboarding friction low.

### Phase 2 (Weeks 5-8): Mission Profile and Governance Productization

- Introduce `MissionProfile` as a first-class entity:
  - tool/integration allowlist
  - governance strictness level
  - required skills/knowledge domains
  - Slack communication bindings
  - notification behavior
- Add mission profile attach/switch flows for missions and agent sessions.
- Add explicit approval queue and lifecycle:
  - `requested -> approved/rejected -> executed/expired`
- Ensure immutable audit linkage between approval artifacts and executed mutations.
- Align MCP tool catalog and philosophy surface area (add missing creation/detection tools or explicitly deprecate claims).

### Phase 3 (Weeks 8-12): Enterprise Reliability, Observability, and Compliance Evidence

- Add enterprise observability:
  - structured logs with request ID, mission ID, actor subject, channel
  - metrics for latency, errors, approval latency, publish latency, queue depth
  - tracing for mutation and approval paths
- Move background ingestion/notification flows to durable queue/outbox pattern with retry, idempotency keys, and dead-letter handling.
- Add retention controls and exportable audit evidence for regulated review workflows.
- Produce operational runbooks (key rotation, approval outage fallback, incident response, rollback drills).
- Complete production test matrix across authz isolation, approval integrity, Slack workflows, and storage profiles.

## API and Interface Additions

### Slack Integration Endpoints

- `POST /integrations/slack/events`
- `POST /integrations/slack/commands`
- `POST /integrations/slack/interactions`

### Approval Workflow Endpoints

- `POST /approvals/requests`
- `POST /approvals/{id}/approve`
- `POST /approvals/{id}/reject`
- `GET /approvals?mission_id=...`

### Mission Profile Endpoints

- `POST /mission-profiles`
- `PATCH /mission-profiles/{id}`
- `POST /missions/{mission_id}/profile`

### MCP Surface Alignment

- Add explicit parity for philosophy-claimed tools (for example mission/kluster creation or overlap detection variants), or formally document naming/deprecation if existing tools differ.
- TODO: Add MCP mutation tools for knowledge ingestion parity with REST (`create_doc`, `update_doc`, `create_artifact`, `update_artifact`) so agents can attach `.md`, code references, and external links without REST-only fallbacks.
- TODO: Add MCP-first attachment/import helpers for local repository paths and URL references (for example `attach_reference_links`, `attach_repo_paths`) to reduce manual task-description stuffing.
- TODO: Investigate and fix `POST /docs` server-side failure in dev (`https://mc.example.com`) returning HTTP 500 for valid payloads; add regression test coverage in API and MCP integration suites.
- TODO: Add explicit kluster workspace lifecycle tools for collaborative file sync: `load_kluster_workspace`, `heartbeat_workspace_lease`, `commit_kluster_workspace`, and `release_kluster_workspace` with lease/lock protection, diff reconciliation, and conflict policy.
- TODO: Add artifact binary/file mutation flow (upload new version, patch metadata, validate checksums, and publish) so agents can do full local edit-reconcile-push loops beyond reference-only artifacts.

## Test Plan and Acceptance Criteria

- Security isolation:
  - principals can only read/search entities in authorized missions across REST, MCP, and Slack
- Approval integrity:
  - unsigned, expired, replayed, or tampered approvals are rejected
  - executed mutations include immutable approver provenance
- Slack MVP:
  - thread-to-task, overlap warning, approval actions, and publish alerts work under mission and role constraints
- Reliability:
  - retries and dead-letter paths handle transient failures without duplicate mutation side effects
- Compliance evidence:
  - audit export includes actor, action, approval linkage, request ID, and timestamps for sensitive mutations

## Key Risks and Mitigations

- Risk: Slack increases external attack surface.
  - Mitigation: strict signature verification, scoped credentials, channel-to-mission authorization, deny-by-default mutation policy.
- Risk: Governance strictness slows adoption.
  - Mitigation: guided Slack UX, clear denial reasons, quick-start templates, read-first defaults.
- Risk: Background failures create state divergence.
  - Mitigation: outbox/idempotency/reconciliation jobs and operational SLOs.

## Assumptions and Defaults

- Enterprise priority is governance/compliance with strong adoption UX.
- Slack delivery is MVP-first, not full platform expansion in first 90 days.
- Production defaults are conservative and least-privilege by design.
- This plan is the canonical enterprise execution reference for the next 90-day cycle.
