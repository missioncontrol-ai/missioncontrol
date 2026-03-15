# MCP Parity Matrix

Updated: `2026-03-15`
Owner: `merlin`

This matrix tracks parity between core REST operations and MCP tools. The MCP path is the primary integration boundary for agents.

## Critical entity lifecycle

| Domain | REST surface | MCP tool | Status | Notes |
| --- | --- | --- | --- | --- |
| Missions | `POST /missions` | `create_mission` | Implemented | Keep authz/governance behavior aligned. |
| Missions | `GET /missions` | `list_missions` | Implemented | Verify pagination/filter parity. |
| Klusters | `POST /klusters` | `create_kluster` | Implemented | Added in recent MCP lifecycle work. |
| Tasks | `POST /tasks` | `create_task` | Implemented | Validate idempotency/replay behavior. |
| Tasks | `PATCH /tasks/{id}` | `update_task` | Implemented | Confirm error envelope consistency. |
| Tasks | `DELETE /tasks/{id}` | `delete_task` | Implemented | Confirm authz parity coverage. |
| Docs | `POST /docs` | `create_doc` | Implemented | Ensure profile/session attribution appears in ledger metadata. |
| Artifacts | `POST /artifacts` | `create_artifact` | Implemented | Confirm object-store failure mapping consistency. |

## Profile lifecycle (cross-machine)

| Domain | REST surface | MCP tool | Status | Notes |
| --- | --- | --- | --- | --- |
| Profiles | `GET /me/profiles` | `list_profiles` | Implemented | Used by `mc init` + `mc launch`. |
| Profiles | `POST /me/profiles` | `publish_profile` | Implemented | Includes SHA conflict checks. |
| Profiles | `GET /me/profiles/{name}` | `get_profile` | Implemented | Used by `mc profile status`. |
| Profiles | `GET /me/profiles/{name}/download` | `download_profile` | Implemented | Supports pull + pin checks. |
| Profiles | `POST /me/profiles/{name}/activate` | `activate_profile` | Implemented | Activation semantics in place. |
| Profiles | `PATCH /me/profiles/{name}` (pin metadata path) | `pin_profile_version` | Implemented | Used in status/pin flows. |

## Operational/governance MCP readiness

| Area | Status | Remaining |
| --- | --- | --- |
| Error envelope consistency | Partial | Normalize tool error codes/envelope + compatibility tests (`MC-MCP-005`). |
| Mutation idempotency/replay safety | Partial | Add deterministic/idempotent behavior and replay coverage (`MC-MCP-007`). |
| Security adversarial regression coverage | Partial | Expand cross-mission/authz/tamper tests (`MC-MCP-012`). |
| Doctor operational diagnostics | Partial | Expand connectivity/auth/tool-surface/snapshot categories in doctor (`MC-MCP-011`). |

## Immediate implementation sequence

1. `MC-MCP-005`: standardize MCP response/error envelope and compatibility tests.
2. `MC-MCP-007`: add mutation idempotency + replay-safety coverage on critical writes.
3. `MC-MCP-012`: complete adversarial security regression suite for MCP critical path.
4. `MC-MCP-013`: enforce release gate on parity/security/doctor/E2E checks.
