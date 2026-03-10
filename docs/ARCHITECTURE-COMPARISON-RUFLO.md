# Architecture Comparison — missioncontrol vs ruvnet/ruflo

## High-level overview

- See [`docs/MC-MCP-RS.md`](MC-MCP-RS.md) for the mc daemon workflows and read [`docs/REAL-TIME.md`](REAL-TIME.md) for the `/events/stream` schema, MQTT relay, and WASM booster wiring that Ruflo-style swarms can consume.
- **Scope**: both platforms orchestrate AI agents, but missioncontrol focuses on a hardened FastAPI/Postgres/MQTT backend with Go-style approvals/telemetry, while Ruflo (Claude Flow v3.5) is a full-stack Rust/TypeScript orchestration framework delivering 60+ specialized agents, RuVector intelligence, WASM boosters, and consensus-driven swarms out of the box.
- **Core layers**: ours is API-first (FastAPI routers + MCP daemon + Docker Compose); Ruflo is CLI/MCP entry → MOE router → swarm coordinator → agent workers → RuVector memory → LLMs, all managed by Rust/WASM runtimes and Next.js UI.
- **Deployment posture**: we target VM/Compose with explicit healthchecks, resource caps, and PoC docs; Ruflo ships npm/pnpm installers (`ruflo init`) plus daemonized background workers, consensus modules (Raft/BFT/Gossip), and a CLI/Next UI stack.

## Capability matrix
| Dimension | missioncontrol | ruvnet/ruflo |
|---|---|---|
| **Auth & security** | Token/OIDC dual-mode with admin whitelist, service token guards, rate limiting, approval policies, Mongo-like governance logs, Compose secrets + TLS-ready proxy. | AIDefence security layer, input validation, prompt injection guards, credential blocking, integrated anti-drift defaults, queen-led consensus (Raft/BFT/Gossip). |
| **Persistence & memory** | SQLModel/Postgres with optional schema migrations, object storage via RustFS/S3, ledger events exported to Git, limited caching/Redis planned. | HNSW vector memory, AgentDB, RuVector intelligence (SONA, EWC++, ReasoningBank, Poincaré embeddings), shared knowledge graph, agent scopes with SQLite WAL persistence, caches for Reusable routes. |
| **Scalability & orchestration** | FastAPI (mostly sync), MQTT inbox, vector store interactions, new rate-limited SSE plan, Compose for scaling; lacks consensus algorithms but has overlapping scoring and governance. | 60+ agents in swarms, queen/workers, consensus algorithms (Byzantine, Weighted, Majority), topologies (mesh/ hierarchical), worker teams, storm resilience, RuVector router with RL learners and MoE/skills. |
| **Realtime & UI** | Static UI via `backend/web`, MCP tooling, SSE matrix idea under discussion, telemetry at `/ops/metrics`, health endpoints. | Next.js UI + CLI, SSE/WebSocket feeds, intelligence dashboard with swarms, queen status, Token Optimizer, Agent Booster, metrics, consensus monitors. |
| **Ops & docs** | Hardened Compose with healthchecks, resource limits, README/DEPLOYMENT.md describing TLS/secret handling, `/healthz`/`/readyz`, telemetry endpoint. | Installer scripts (`install.sh`), CLI wizards (`ruflo init`), multi-profile install, security/ops docs, analytics dashboards, background workers auto-run audits and learning loops. |

## Improvement opportunities inspired by Ruflo
1. **Consensus + fault-tolerant swarms**: implement lightweight consensus (Raft/BFT) for mission-critical approvals or ledger publishing to match Ruflo’s queen topology; extend `backend/app/services/git_ledger.py` with quorum voting before publishing.
2. **Vector intelligence layer**: build a RuVector-inspired memory module that tracks reasoning patterns (reuse `backend/app/services/overlap.py`, add SONA/EWC-style metadata, leverage `backend/app/services/object_storage.py` for caching). Document it alongside the existing matrix plan.
3. **Agent Booster / WASM fallback**: add hook-level fast-path transforms (e.g., simple code fixes) similar to Ruflo’s Agent Booster by detecting simple patterns in `backend/app/services/schema_pack.py` or CLI hooks and short-circuiting LLM calls.
4. **Ops experience**: publish a `docs/REAL-TIME.md` or release checklist referencing `docs/ARCHITECTURE-COMPARISON.md`, mirroring Ruflo’s install profiles and analytics; capture health/metrics expectations as they do with `Doctor` scripts.
5. **Heatmap of agent collaboration**: document the “matrix” by showing how MQTT inbox + approvals + telemetry can be consumed via SSE/WebSocket to mimic Ruflo’s hive mind dashboard with queen statuses, bounding latencies with our rate-limit middleware.
6. **Doctor telemetry + self-repair**: expand the `docs/MC-MCP-RS.md` guide so readers know `mc doctor` now covers health/tools/matrix probes, emits the matrix schema described in `docs/REAL-TIME.md`, and can run `--repair` to persist agent IDs and ensure local directories before spinning up Ruflo/OpenClaw swarms.

## Tests & validation
- Existing suite remains the source of truth (`PYTHONPATH=backend .venv/bin/python -m unittest discover backend/tests`).  
- Manual verification: review the new doc, confirm it references `backend/app/main.py`, `backend/app/services/authz.py`, and `docs/ARCHITECTURE-COMPARISON.md`; optionally compare against the Ruflo README for accuracy.

## Assumptions
- Focus is on comparative documentation and strategic insights, not implementing Ruflo features immediately.  
- The Ruflo README is the primary source for their architecture; we are not cloning their stack but identifying high-level improvements.  
- No additional automated tests are needed beyond the existing suite already run earlier.
