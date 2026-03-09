# AI Mission Control (Pilot)

A local-first pilot implementation of the AI Mission Control platform: knowledge clusters, task coordination with overlap detection, MCP-style agent tools, and ingestion stubs. Now includes a local vector store for search + similarity.

## Docker Dev (Recommended)

Quickstart profile (SQLite + Chroma fallback):

1. Start API + MQTT:

```bash
bash scripts/dev-up.sh
```

2. Smoke test:

```bash
curl -H "Authorization: Bearer Change-Now-Socrates-Plato-Aristotle-Aurelius" http://localhost:8008/
```

or run automated profile smoke checks:

```bash
bash scripts/smoke.sh --profile both
```

3. Stop:

```bash
bash scripts/dev-down.sh
```

### RustFS Object Storage (Optional, Recommended)

MissionControl supports RustFS/S3-backed docs and artifact content persistence.

Set these in `.env` before `dev-up`/`docker compose up` (preferred `MC_*` aliases shown):

```bash
MC_OBJECT_STORAGE_ENDPOINT=http://<rustfs-host>:<port>
MC_OBJECT_STORAGE_REGION=us-east-1
MC_OBJECT_STORAGE_BUCKET=missioncontrol-dev
MC_OBJECT_STORAGE_SECURE=false
MC_OBJECT_STORAGE_ACCESS_KEY=<key>
MC_OBJECT_STORAGE_ACCESS_SECRET=<secret>
```

Notes:
- MissionControl uses a single service account key/secret.
- Access control remains mission/kluster scoped in the API layer.
- S3 object keys are scoped to `missions/{mission_id}/klusters/{kluster_id}/...`.
- Legacy env names remain supported: `MC_OBJECT_STORAGE_*`, `MC_OBJECT_STORAGE_ACCESS_KEY`, `MC_OBJECT_STORAGE_ACCESS_SECRET`.

Full stack profile (Postgres + pgvector + MQTT):

1. Start full stack:

```bash
bash scripts/dev-up-full.sh
```

2. Stop full stack:

```bash
bash scripts/dev-down-full.sh
```

## Docker Dev (Cluster-Backed)

Use this mode when you want fast local API iteration but keep using the deployed
MissionControl Postgres/MQTT in Kubernetes.

1. Start local API container against cluster services:

```bash
bash scripts/dev-up-cluster.sh
```

2. Stop:

```bash
bash scripts/dev-down-cluster.sh
```

3. The script prints the host IP it is bound to.
4. Optional: export `GIT_PUBLISH_*` env vars before running the script if you want
artifact publish to commit/push into a Git repo during local cluster-backed testing.
If `GIT_PUBLISH_TOKEN` is not exported, the script will also check Kubernetes secret
`missioncontrol-git-publish` key `GIT_PUBLISH_TOKEN` in namespace `missioncontrol`.

## Quickstart (Python)

0. Create env file:

```bash
cp .env.example .env
```

Set OIDC env vars in `.env` for preferred auth mode, or set `MC_TOKEN` for static-token fallback mode.
MQTT settings are also available for agent messaging (see `.env.example`).

1. Install backend deps:

```powershell
cd C:\code\taskman\backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

2. Run the API:

Load env vars first (bash/zsh):

```bash
set -a; source .env; set +a
```

```powershell
uvicorn app.main:app --reload
```

3. Open the UI:

Open `http://localhost:8008/ui/` in a browser.

## API Overview

- `GET /missions/{mission_id}/k` + `POST /missions/{mission_id}/k`
- `DELETE /missions/{mission_id}/k/{kluster_id}`
- `GET /missions` + `POST /missions` + `DELETE /missions/{mission_id}`
- `POST /missions/{mission_id}/owner` (admin only)
- `GET /missions/{mission_id}/roles` + `POST /missions/{mission_id}/roles` + `DELETE /missions/{mission_id}/roles/{subject}`
- `GET /docs` + `POST /docs` + `POST /docs/{id}/publish`
- `GET /artifacts` + `POST /artifacts` + `POST /artifacts/{id}/publish`
- `GET /tasks` + `POST /tasks` + `GET /tasks/{id}/overlaps`
- `DELETE /tasks/{id}`
- `POST /ingest/github` + `POST /ingest/drive` + `POST /ingest/slack`
- `GET /ingest/jobs` + `GET /ingest/jobs/{id}`
- `GET /search/tasks?q=...` + `GET /search/docs?q=...`
- `GET /search/klusters?q=...`
- `GET /schema-pack`
- `GET /explorer/tree` + `GET /explorer/node/{node_type}/{node_id}`
- `GET /agent-onboarding.json` (machine-readable agent activation manifest)
- `GET /governance/policy/active` (effective governance policy)
- `POST /governance/policy/drafts` + publish/rollback admin endpoints
- `POST /approvals/requests` + `GET /approvals` + approve/reject endpoints
- `POST /integrations/slack/events` + `POST /integrations/slack/commands` + `POST /integrations/slack/interactions`
- `POST /integrations/chat/bindings` + `GET /integrations/chat/bindings` + `DELETE /integrations/chat/bindings/{id}`
- `POST /integrations/google-chat/events` (provider skeleton)
- `POST /integrations/teams/events` (provider skeleton)
- `POST /integrations/slack/bindings` + `GET /integrations/slack/bindings` + `DELETE /integrations/slack/bindings/{id}`
- `POST /feedback/agent` + `POST /feedback/human` + `GET /feedback?mission_id=...` + `PATCH /feedback/{id}/triage` + `GET /feedback/summary?mission_id=...`
- `GET /mcp/health` + `GET /mcp/tools` + `POST /mcp/call`
- `GET /ops/metrics` (platform admin only)
- `GET /ops/logs?limit=200` (platform admin only)

All API responses include an `x-request-id` header for correlation and tracing.
Set `MC_LOG_EXPORT_PATH=/abs/path/missioncontrol.jsonl` to export structured events as JSON lines.

## Agent Integration (Codex + Claude)

- MCP bridge package: `integrations/missioncontrol-mcp`
- MCP doctor: `missioncontrol-mcp doctor`
- MCP lifecycle validation playbook: `scripts/mcp-validation-playbook.sh` (docs: `docs/MCP-VALIDATION-PLAYBOOK.md`)
- Explorer CLI add-on: `missioncontrol-explorer tree --format ansi`
- Config generator: `bash scripts/generate-agent-config.sh --base-url http://localhost:8008 --agent all --out ./generated-agent-config`
- Team install/run guide: `docs/AGENT-INSTALL.md`
- Public integration subtree source: `distribution/mc-integration`
- Public integration release runbook: `docs/MC-INTEGRATION-SUBTREE.md`
- Subtree release helper: `bash scripts/release-mc-integration-subtree.sh`
- API supports `AUTH_MODE=token|oidc|dual`.
- Runtime default preference is OIDC. If `AUTH_MODE` is unset and only `MC_TOKEN` is configured, startup falls back to token mode.

## MCP Examples

```bash
curl http://localhost:8008/mcp/tools
```

```bash
curl -X POST http://localhost:8008/mcp/call -H "Content-Type: application/json" -d '{"tool":"search_klusters","args":{"query":"marketing"}}'
```

```bash
curl -X POST http://localhost:8008/mcp/call -H "Content-Type: application/json" -d '{"tool":"search_tasks","args":{"query":"overlap detection","limit":5}}'
```

```bash
curl -X POST http://localhost:8008/mcp/call -H "Content-Type: application/json" -d '{"tool":"load_kluster_workspace","args":{"kluster_id":"<kluster-id>"}}'
```

## Ingestion Examples

```bash
curl -X POST http://localhost:8008/ingest/github -H "Content-Type: application/json" -d '{"cluster_id":"abc123def456","config":{"repo":"org/repo"}}'
```

```bash
curl http://localhost:8008/ingest/jobs?cluster_id=abc123def456
```

## Git Publisher (Optional)

`POST /artifacts/{id}/publish` always marks the artifact as `published` in Postgres.

If `GIT_PUBLISH_ENABLED=true`, the publish endpoint also writes an artifact record JSON
to a Git repository and pushes it to the configured branch. On success, artifact
`provenance` is populated with repo/branch/path/commit metadata.

Ledger workflow:
- Create actions (mission/cluster/task) attempt immediate bootstrap Git commit to initialize location with pending metadata.
- Non-create mutations are recorded as pending ledger events in DB (explicit publish flow).
- Use MCP `publish_pending_ledger_events` (mission-scoped) to persist pending events to Git.
- Use MCP `list_pending_ledger_events` and `get_entity_history` for pre-publish visibility.

Artifact layout defaults:
- `GIT_PUBLISH_LAYOUT_VERSION=v2`
- `GIT_PUBLISH_BASE_PATH=artifacts`
- v2 uses hash-bucketed key+slug paths with mission folder format `{mission_slug}-{mission_hash}`.

## Search Examples

```bash
curl "http://localhost:8008/search/tasks?q=roadmap"
```

```bash
curl "http://localhost:8008/search/docs?q=architecture"
```

```bash
curl "http://localhost:8008/search/klusters?q=mission"
```

## DB Migrations

Alembic is available for forward schema migrations:

```bash
cd backend
alembic upgrade head
```

Migration CI workflow: `.github/workflows/ci-migrations.yml`

Release procedure checklist: `docs/RELEASE-UPGRADE-CHECKLIST.md`

## Tests

Run the repo test suites (backend + MCP bridge package) from repo root:

```bash
bash scripts/test.sh
```

## Schema Packs

- Default schema pack file: `docs/schema-packs/main.json`
- Alternative example: `docs/schema-packs/alternative-product.json`
- Override active pack with env var `MC_SCHEMA_PACK_FILE=/abs/path/to/pack.json`

## Governance Policy

- Governance policy is DB-backed and versioned (`draft` -> `active`).
- Admin UI tab (`/ui` -> Admin) supports viewing/editing/publishing policy.
- Optional conservative preset:
  - `MC_GOV_PROFILE=production` (forces mutation approvals and disables mutation via MCP/terminal by default)
- Optional env overrides (highest precedence):
  - `MC_GOV_REQUIRE_APPROVAL_FOR_MUTATIONS`
  - `MC_GOV_ALLOW_CREATE_WITHOUT_APPROVAL`
  - `MC_GOV_ALLOW_UPDATE`
  - `MC_GOV_ALLOW_DELETE`
  - `MC_GOV_ALLOW_PUBLISH`
  - `MC_GOV_MCP_ALLOW_MUTATION_TOOLS`
  - `MC_GOV_TERMINAL_ALLOW_CREATE_ACTIONS`
  - `MC_GOV_TERMINAL_ALLOW_PUBLISH_ACTIONS`
  - `MC_APPROVAL_TOKEN_SECRET`
  - `MC_ALLOW_LEGACY_APPROVAL_CONTEXT`

Approval token header:
- `x-approval-token` (HMAC-signed payload with `request_id`, `approved_by`, `approved_at`, `exp`, `nonce`)
- mutation responses include trace headers when approval is used:
  - `x-approval-request-id`

## Slack Outbound Notifications

- Configure `SLACK_BOT_TOKEN` to enable MissionControl event notifications into bound Slack channels.
- Slack channel bindings are now provider-aware and default to `provider=slack`.
- New provider-agnostic bindings API is available at `/integrations/chat/bindings`; Slack binding endpoints are preserved for compatibility.

## Google Chat Provider Skeleton

- Configure `GOOGLE_CHAT_VERIFICATION_TOKEN` for inbound event verification.
- Reuse bindings API with `provider=google_chat`.
- For outbound notifications, set binding `channel_metadata.webhook_url` to a Google Chat incoming webhook URL.

## Teams Provider Skeleton

- Configure `TEAMS_VERIFICATION_TOKEN` for inbound event verification.
- Reuse bindings API with `provider=teams`.
- For outbound notifications, set binding `channel_metadata.webhook_url` to a Teams incoming webhook URL.
  - `x-approval-nonce`
- optional event sink for approval lifecycle notifications:
  - `MC_NOTIFICATION_WEBHOOK_URL`
- Slack request verification:
  - `SLACK_SIGNING_SECRET`
  - `SLACK_SIGNATURE_TOLERANCE_SEC`
- Slack role mapping for ChatOps:
  - map Slack users to mission roles via subjects in format `slack:<SLACK_USER_ID>`

## Notes

- Docker default database is Postgres (`pgvector/pgvector:0.8.2-pg18-trixie` image).
- Set CORS allowlist via `MC_CORS_ALLOW_ORIGINS` (comma-separated origins).
- Default vector store is pgvector when using Postgres.
- Quickstart fallback database remains SQLite (`backend/taskman.db`) with Chroma (`backend/chroma`) vector fallback.
- You can force vector backend with `VECTOR_STORE_BACKEND=pgvector|chroma`.
- Embeddings are deterministic hash-based for the pilot; replace with OpenAI/Claude/Ollama adapters.
- Overlap detection uses both fuzzy similarity and vector search.
- Ingestion endpoints enqueue stub jobs and create a placeholder doc.

## License and Trademark

- License: Apache-2.0 (`LICENSE`)
- Notice: `NOTICE`
- Trademark usage: `TRADEMARK_POLICY.md`

## Contributing and Community

- Contribution guide: `CONTRIBUTING.md`
- Code of conduct: `CODE_OF_CONDUCT.md`
- Security reporting: `SECURITY.md`
- Support boundaries: `SUPPORT.md`
- Project governance: `GOVERNANCE.md`

## Open Core and Public Readiness

- Open core model: `docs/OPEN-CORE-MODEL.md`
- Public readiness checklist: `docs/PUBLIC-READINESS-CHECKLIST.md`
