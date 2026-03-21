# ADR 0002: Source-of-Truth Boundaries

**Status:** Accepted
**Date:** 2026-03-21

## Context

Without explicit boundaries, the catalog risks becoming a second source of truth for implementation detail, duplicating OpenAPI schemas, narrative docs, and code behavior.

## Decision

| Layer | Source of Truth | Location |
|-------|----------------|----------|
| Subsystem inventory, status, navigation | Catalog | `docs/catalog/*.yaml` |
| Narrative and operator guidance | Markdown docs | `docs/*.md`, `README.md` |
| HTTP API contract | OpenAPI schema | `/api/docs` (generated) |
| Implementation behavior | Code and tests | `backend/`, `web/` |
| Architectural decisions | ADRs | `docs/adr/` |

## Consequences

- Catalog files stay concise (navigational only, ~80 lines max)
- OpenAPI is the authoritative reference for endpoint schemas — catalog references it, does not re-encode it
- When adding a new subsystem: add a domain YAML, update index.yaml, run validator
