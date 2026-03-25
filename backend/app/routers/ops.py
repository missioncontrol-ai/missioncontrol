import json
import os
import subprocess
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.services.authz import actor_subject_from_request, ensure_platform_admin
from app.services.ids import new_hash_id
from app.services.log_export import emit_structured_log
from app.services.secrets import bootstrap_profile_secrets, rotate_profile_secret, secrets_status

router = APIRouter(prefix="/ops", tags=["ops"])

BACKUP_DIR = Path(os.getenv("MC_HOME", "~/.missioncontrol")).expanduser() / "backups"
BACKUP_FILE = BACKUP_DIR / "records.json"


class BackupRequest(BaseModel):
    target: str
    reason: str | None = None


class BackupRecord(BaseModel):
    id: str
    target: str
    reason: str | None
    triggered_by: str
    status: str
    created_at: str


class SecretsBootstrapRequest(BaseModel):
    profile: str = "default"
    provider: str = "env"
    keep_existing: bool = True
    infisical_project_id: str | None = None
    infisical_env: str | None = None
    infisical_path: str | None = None


class SecretsRotateRequest(BaseModel):
    profile: str = "default"
    name: str
    value: str | None = None
    generator: str = "token"


@router.post("/backups")
def trigger_backup(payload: BackupRequest, request: Request):
    ensure_platform_admin(request)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    record = BackupRecord(
        id=new_hash_id(),
        target=payload.target,
        reason=payload.reason,
        triggered_by=actor_subject_from_request(request),
        status="scheduled",
        created_at=datetime.utcnow().isoformat() + "Z",
    )
    records = []
    if BACKUP_FILE.exists():
        try:
            records = json.loads(BACKUP_FILE.read_text())
        except Exception:
            records = []
    records.append(record.dict())
    BACKUP_FILE.write_text(json.dumps(records, indent=2))
    script = os.getenv("MC_BACKUP_SCRIPT")
    if script:
        try:
            subprocess.Popen([script, payload.target], env=dict(os.environ, BACKUP_ID=record.id))
            record.status = "running"
        except Exception as exc:  # pragma: no cover
            record.status = f"failed: {exc}"
    return {"ok": True, "backup": record}


@router.get("/backups")
def list_backups(request: Request):
    ensure_platform_admin(request)
    if not BACKUP_FILE.exists():
        return {"backups": []}
    try:
        records = json.loads(BACKUP_FILE.read_text())
    except Exception:
        records = []
    return {"backups": records}


@router.get("/secrets/status")
def get_secrets_status(request: Request):
    ensure_platform_admin(request)
    return {"secrets": secrets_status()}


@router.post("/secrets/bootstrap")
def post_secrets_bootstrap(payload: SecretsBootstrapRequest, request: Request):
    ensure_platform_admin(request)
    profile_name = (payload.profile or "default").strip() or "default"
    provider = (payload.provider or "env").strip().lower()
    if provider not in {"env", "infisical"}:
        return {"ok": False, "error": "provider must be env or infisical"}
    result = bootstrap_profile_secrets(
        profile_name=profile_name,
        provider=provider,
        keep_existing=bool(payload.keep_existing),
        infisical_project_id=(payload.infisical_project_id or "").strip() or None,
        infisical_env=(payload.infisical_env or "").strip() or None,
        infisical_path=(payload.infisical_path or "").strip() or None,
    )
    emit_structured_log(
        {
            "event_type": "secrets.bootstrap",
            "channel": "governance",
            "actor_subject": actor_subject_from_request(request),
            "profile": profile_name,
            "provider": provider,
            "refs_count": result.get("refs_count", 0),
        }
    )
    return {"ok": True, "result": result}


@router.post("/secrets/rotate")
def post_secrets_rotate(payload: SecretsRotateRequest, request: Request):
    ensure_platform_admin(request)
    profile_name = (payload.profile or "default").strip() or "default"
    secret_name = (payload.name or "").strip()
    if not secret_name:
        return {"ok": False, "error": "name is required"}
    try:
        result = rotate_profile_secret(
            profile_name=profile_name,
            name=secret_name,
            value=payload.value,
            generator=payload.generator,
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    emit_structured_log(
        {
            "event_type": "secrets.rotate",
            "channel": "governance",
            "actor_subject": actor_subject_from_request(request),
            "profile": profile_name,
            "secret_name": secret_name,
            "provider": result.get("provider", ""),
        }
    )
    return {"ok": True, "result": result}
