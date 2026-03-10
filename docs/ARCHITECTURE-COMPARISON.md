# Architecture Comparison — missioncontrol vs builderz-labs/mission-control

## High-level alignment
- **Core goal**: both repos orchestrate AI agent fleets, surface task boards, and enforce governance.  
- **Stack divergence**: our backend is FastAPI/SQLModel with Postgres, RustFS/S3, MQTT, and a separate MCP daemon. builderz-labs is a single Next.js + TypeScript monolith (sqlite WAL, Zustand, pino) that owns UI + API + scheduler + websocket/SSE pushes.
- **Deployment posture**: we now push hardened Compose (healthchecks, resource caps, token secrets, TLS-ready proxy) plus docs for VM/Compose. builderz-labs runs pnpm + SQLite, with releases featuring Docker + preflight scaffolding but heavier UI focus.

## Feature comparison table
| Dimension | missioncontrol (this repo) | builderz-labs/mission-control |
|-----------|---------------------------|-------------------------------|
| Auth & governance | Token/OIDC dual-mode with admin allowlists, telemetry/logging, approvals, governance policies, owner constraints, rate limiting, `/healthz`/`/readyz`, and secret-y environment requirements. | Viewer/operator/admin RBAC with session/API key/Google login, Zod validation, rate-limited auth routes, security headers, CSP notes, device identity for gateways. |
| Backend & persistence | FastAPI + SQLModel, Postgres with pooling, runtime migrations optional, object persistence via RustFS/S3, MQTT for agent messaging, async guard planned. | Next.js API routes (66+), better-sqlite3 WAL, stateful scheduler, background cron + Claude scanner, WebSocket/SSE feeds, multi-tenant workspace isolation. |
| UI / Real-time | React UI is separate `backend/web` static served; MCP agent on daemons. | Next.js 28-panel dashboard, Kanban board, token/cost charts, SSE + WebSocket feed, CLI integration, update checker. |
| Observability & ops | Structured logs + telemetry module (ops endpoints), DB pooling, Compose healthchecks, new docs for backups/backups not fully automated, rate limits, caching (planned). | Scheduler with cron tasks, structured logging via pino, Vitest/Playwright tests, release checks, mem/cost metrics, webhook retry/circuit breakers. |
| Integrations | Git publish, Slack/Teams/Google webhooks, MCP tooling, workspace sync, approvals, teleport-like agent ledger. | Webhooks with retry/backoff, GitHub issue sync, Claude session scanner, multi-gateway (OpenClaw) management, workspace tenant panel. |

## What we can borrow / improve
1. **Realtime experience**: builderz-labs pushes SSE/WebSocket feeds and UI panels. We can tie our MQTT-backed inbox and real-time telemetry into a status panel or SSE stream to match that feeling.  
2. **RBAC & admin flows**: their viewer/operator/admin roles plus explicit session/API-key handling go beyond our owner/contributor/admin checks. Consider exposing explicit roles and API keys with rotation/tracing.  
3. **Background jobs + scheduler**: we already have approvals + backup policy seeds, but builderz-labs has scheduler + retries for cron jobs and webhooks. Expand our resilience story with retry queues, dead letter logging, and a cron dashboard.  
4. **Unity of docs/testing**: they ship `CHANGELOG`, `Vitest`/`Playwright`, strong release gating. We already run pytest suites and doc Compose health, so add a `RELEASE.md` or smoke-checker script aligning with builderz’s quality gate table.  
5. **Security telemetry**: builderz-labs enforces TLS, CSP, device identity, HSTS, and has `SECURITY.md`. We can mirror by publishing our attack surface doc, enumerating TLS requirements, rotating keys, and enumerating admin audit logs.

## Evaluation criteria
- Keep existing automated tests green (`python -m unittest discover backend/tests`).  
- Document any remaining warnings/deprecations (Pydantic `.dict()`, `datetime.utcnow()`) in `docs/TECH-ALERTS.md` or future backlog so reviewers know they are acknowledged.  
- Validate Compose health endpoints manually (not runnable here) once stack can run with env secrets.

## Next steps
1. Create a README appendix or doc summarizing the comparison for stakeholders (this file).  
2. Spin up the hardened Compose stack and hit `/healthz`, `/readyz`, `/ops/metrics` to confirm they align with the builderz expectations of operational maturity.  
3. Track future work items: SSE/MQTT panel, RBAC roles, scheduler dashboard, release/runbook doc.
