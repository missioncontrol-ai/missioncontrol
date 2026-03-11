# MissionControl OIDC (Authentik)

This repo supports OIDC JWT validation in the MissionControl API while keeping token auth for MCP compatibility.

Production requirement: manage secret values through Kubernetes Secrets (no inline literals, no checked-in `.env`).

## MissionControl API env

Set these on the `missioncontrol-api` deployment:

```env
AUTH_MODE=oidc
OIDC_REQUIRED=false
MC_TOKEN=<static-token-for-mcp>
OIDC_ISSUER_URL=https://<authentik-host>/application/o/<provider-slug>/
OIDC_AUDIENCE=<oidc-client-id>
MC_ADMIN_SUBJECTS=<comma-separated-subjects>
MC_ADMIN_EMAILS=<comma-separated-emails>
# optional
# OIDC_JWKS_URL=https://<authentik-host>/application/o/<provider-slug>/jwks/
```

Modes:
- `AUTH_MODE=token`: static bearer token only.
- `AUTH_MODE=oidc`: OIDC JWT only.
- `AUTH_MODE=dual`: accept token and OIDC.

`OIDC_REQUIRED=true` in dual mode enforces OIDC for non-`/mcp` paths.
If `AUTH_MODE` is unset, runtime defaults to OIDC when OIDC vars are present, and falls back to token mode when only `MC_TOKEN` is configured.

## Kubernetes secret guidance

- Source all auth settings from Kubernetes Secrets.
- Do not commit client secrets, service tokens, or static `MC_TOKEN` values.
- Mount/inject only secret refs in manifests (`envFrom.secretRef` / `env.valueFrom.secretKeyRef`).
- Roll out with:
  1. `AUTH_MODE=oidc`, `OIDC_REQUIRED=false`
  2. validate MissionControl user flows
  3. optionally use `AUTH_MODE=dual` for staged MCP migration
  4. optionally set `OIDC_REQUIRED=true`
  5. later migrate MCP to service-account OIDC and remove static token
