# Codex Multi-Session Swarm Workflow (No Nested `codex exec`)

This is the canonical Codex-native swarm workflow for MissionControl.

Goal: run multiple Codex sessions collaborating on the same mission/kluster without using nested `codex exec` pressure workers.

## Why this path

- Avoids nested MCP handshake contention from `codex exec` inside `codex exec`.
- Uses the same production control plane (`mc daemon` shim + MissionControl API).
- Produces run artifacts for replay/debug (`artifacts/collab/<run_id>/summary.json`).

## Prerequisites

- MissionControl quickstart stack healthy (`api`, `mcpd`, `mosquitto`, `rustfs`).
- Local shim reachable at `127.0.0.1:8765`.
- `MC_TOKEN` valid for your API.

## 1) Start a collaboration run (driver session)

Run in one terminal:

```bash
MC_BASE_URL=http://localhost:8008 \
MC_TOKEN="<token>" \
MC_COLLAB_DURATION_SEC=600 \
MC_COLLAB_POLL_SEC=5 \
bash scripts/mc-collab-driver.sh
```

Driver behavior:

- creates mission + kluster + 3 scenario tasks (unless you provide existing IDs)
- prints `mission_id` and `kluster_id`
- samples task state during the run window
- emits `summary.json` at completion

Attach to an existing mission/kluster instead:

```bash
MC_BASE_URL=http://localhost:8008 \
MC_TOKEN="<token>" \
MC_COLLAB_MISSION_ID="<mission_id>" \
MC_COLLAB_KLUSTER_ID="<kluster_id>" \
bash scripts/mc-collab-driver.sh
```

## 2) Join with additional Codex sessions

Open 2-5 additional Codex sessions.

Each session should:

- use MissionControl MCP shim (`MC_MCP_MODE=shim`, daemon `127.0.0.1:8765`)
- target the same `mission_id`/`kluster_id`
- pick one task and move it through: `proposed -> in_progress -> blocked|done`
- include concise updates in task descriptions

Recommended role split:

- Session A: Runtime/debug (MCP handshake, daemon path)
- Session B: Harness instrumentation (reporting, categorization)
- Session C: Ops docs/runbook updates

## 3) Collaboration protocol

- Claim: update task status to `in_progress` + ownership note.
- Work: append concrete findings/changes (not generic notes).
- Handoff: if blocked, set `blocked` + explicit unblock requirement.
- Complete: set `done` + short outcome summary.

Keep writes intentional; do not spam status flaps.

## 4) Acceptance criteria

A run is considered healthy when:

- at least 3 tasks exist in the target kluster
- each task has at least one meaningful state transition
- at least one task reaches `done`
- driver report shows non-zero `observed_changes`

Check:

```bash
cat artifacts/collab/<run_id>/summary.json
```

Key fields:

- `observed_changes`
- `status_counts`
- `tasks[]` (`status`, `updated_at`)

## 5) Troubleshooting

- If `mcpd` fails to start on `:8765`, stop any local `mc daemon` already bound there.
- If Codex reports MCP startup incomplete, verify `MC_*` env names (not `MISSIONCONTROL_*`).
- If Mosquitto blocks startup, run `bash scripts/dev-up.sh` (it now clears preexisting/orphan containers first).

## 6) Recommended pressure sequence

1. `playbook` gate (`scripts/mc-pressure-test.sh`) as deterministic baseline.
2. Codex multi-session swarm run (this doc) for real collaboration behavior.
3. Only after both are stable, revisit nested `agent` pressure mode.
