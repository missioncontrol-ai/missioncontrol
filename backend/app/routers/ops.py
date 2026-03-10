import json
import os
import subprocess
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.services.authz import actor_subject_from_request, ensure_platform_admin
from app.services.ids import new_hash_id

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
