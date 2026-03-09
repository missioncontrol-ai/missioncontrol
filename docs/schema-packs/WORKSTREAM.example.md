# WORKSTREAM: <kluster-name>

## Purpose
- Kluster ID: `<kluster-id>`
- Mission ID: `<mission-id>`
- Description: <human-readable workstream objective>

## Governance
- Owners: <comma-separated owners>
- Contributors: <comma-separated contributors>
- Rules: <kluster-specific rules>
- Allowed Actions: <kluster-specific allow/deny policy>

## Policy Overlay
- Inherits Mission Policy: yes|no
- Override Scope: <explicit overrides from NORTHSTAR>

## External Storage
- Object Prefix: `missions/<mission-id>/klusters/<kluster-id>/`
- Credential Refs: `secretref://infisical/<project>/<path>#<key>` (no plaintext)

## Integrations
- Tools/Connections: <refs>
- Auth Refs: <secret refs only>

## Data Sources
- Databases/APIs: <refs and usage constraints>

## Agent Runtime
- Agent Profiles: <profile names>
- AGENT.md Refs: <paths/ids>
- Required Capabilities: <capability list>

## Versioning
- Version: 1
- Created By: <subject>
- Modified By: <subject>
- Change Summary: <what changed>
