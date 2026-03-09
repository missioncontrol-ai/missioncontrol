# NORTHSTAR: <mission-name>

## Purpose
- Mission ID: `<mission-id>`
- Description: <human-readable mission objective>

## Governance
- Owners: <comma-separated owners>
- Contributors: <comma-separated contributors>
- Rules: <mission-wide rules and constraints>
- Allowed Actions: <mission-wide action allow/deny policy>

## Policy
- Enforcement Mode: overlay|enforce
- Policy Refs: <policy docs/ids>

## External Storage
- Object Store: <provider/endpoint/bucket>
- Prefix: `missions/<mission-id>/...`
- Credentials: `secretref://infisical/<project>/<path>#<key>` (no plaintext)

## Integrations
- Connections: <service/tool connection refs>
- Auth Refs: <secret refs only>

## Data Sources
- Databases/APIs: <data source refs and access pattern>

## Agent Runtime
- Agent Profiles: <profile names>
- AGENT.md Refs: <paths/ids>
- Required Capabilities: <capability list>

## Versioning
- Version: 1
- Created By: <subject>
- Modified By: <subject>
- Change Summary: <what changed>
