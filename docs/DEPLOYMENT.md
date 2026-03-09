# MissionControl Deployment Guide (Linux VM)

This guide covers deploying MissionControl API on a Linux VM.

## Objectives
- Run MissionControl API as a system service.
- Configure auth via OIDC and/or static MC token.
- Configure optional RustFS/S3 object storage for docs/artifacts.

## MissionControl API (Linux VM)

### 1) Place code
Recommended path:
- `/opt/missioncontrol/backend`

### 2) Python venv + deps
```bash
cd /opt/missioncontrol/backend
python3 -m venv /opt/missioncontrol/.venv
/opt/missioncontrol/.venv/bin/pip install -r requirements.txt
```

### 3) Environment file
Create `/opt/missioncontrol/.env` (minimum example):

```env
AUTH_MODE=dual
OIDC_REQUIRED=false
MC_TOKEN=<static-token-for-mcp>
OIDC_ISSUER_URL=https://<authentik-host>/application/o/<provider-slug>/
OIDC_AUDIENCE=<oidc-client-id>
```

Optional RustFS/S3-backed doc/artifact content:

```env
MC_OBJECT_STORAGE_ENDPOINT=http://<rustfs-host>:<port>
MC_OBJECT_STORAGE_REGION=us-east-1
MC_OBJECT_STORAGE_BUCKET=missioncontrol
MC_OBJECT_STORAGE_SECURE=false
MC_OBJECT_STORAGE_ACCESS_KEY=<access-key>
MC_OBJECT_STORAGE_ACCESS_SECRET=<secret>
```

### 4) systemd service
Create `/etc/systemd/system/missioncontrol.service`:

```ini
[Unit]
Description=MissionControl API
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/missioncontrol/backend
ExecStart=/opt/missioncontrol/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
Restart=on-failure
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=/opt/missioncontrol/.env

[Install]
WantedBy=multi-user.target
```

Enable + start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now missioncontrol
```

Verify:
```bash
curl http://localhost:8000/
```

## Kubernetes Note

In Kubernetes, do not use `.env` files. Inject env vars from `InfisicalSecret` managed Secrets
into the deployment (`envFrom.secretRef` / `env.valueFrom.secretKeyRef`).

## Validation Checklist
- `curl http://localhost:8000/` returns status ok.
- `curl -H "Authorization: Bearer <MC_TOKEN>" http://localhost:8000/mcp/health` returns ok.
- Docs/artifacts create + delete paths work with expected mission authz.
