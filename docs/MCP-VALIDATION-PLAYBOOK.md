# MCP Validation Playbook

This playbook runs a live lifecycle validation against MissionControl using MCP + API.

## What it covers

- Create mission (MCP)
- Create kluster (MCP)
- Create/list/update/delete task (MCP)
- Create doc (MCP) + update doc (API)
- Create artifact (MCP) + update artifact (API)
- Load/commit/release kluster workspace (MCP)
- Cleanup attempt (delete kluster/mission)

## Script

`scripts/mcp-validation-playbook.sh`

## Prerequisites

- Running API (default `http://localhost:8008`)
- Auth token exported as `MC_TOKEN`
- `jq` and `curl` installed

## Run

```bash
export MC_BASE_URL=http://localhost:8008
export MC_TOKEN="<token>"
scripts/mcp-validation-playbook.sh
```

Optional variables:

- `MC_PLAYBOOK_ACTOR` (default: `ops@example.com`)
- `MC_PLAYBOOK_RUN_ID` (default: timestamp)

## Notes

- Current API does not expose delete endpoints for docs/artifacts.
- Because kluster delete enforces no linked entities, cleanup may stop at HTTP 409 after validation.
- This is expected and is reported explicitly by the playbook.
