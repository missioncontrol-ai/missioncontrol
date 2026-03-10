# Architecture 

## Core Services
- **Knowledge Service**: clusters, docs, artifacts with publish/draft status.
- **Task Service**: tasks, epics, dependencies, overlap suggestions.
- **Overlap Detection**: fuzzy similarity over task text with evidence.
- **Agent Gateway**: MCP-style tool registry and call endpoint.

## Data Flow
1. Human creates tasks in the UI.
2. Task creation triggers overlap suggestions (top 5 matches).
3. Agents can call MCP tools to search, read, and create tasks.
4. All activity is stored in SQLite with audit-ready timestamps.

## Extension Points
- Replace overlap scoring with embedding-based retrieval.
- Add graph DB for entity relationships.
- Move storage to Postgres + S3 for production.
