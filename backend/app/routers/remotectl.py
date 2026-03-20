"""Remote agent launch — target registry and launch tracking."""

import hashlib
import os
import secrets
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlmodel import select

from app.db import get_session
from app.models import RemoteLaunchRecord, RemoteTarget, UserSession
from app.services.authz import actor_subject_from_request

router = APIRouter(prefix="/remotectl", tags=["remotectl"])


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class TargetCreate(BaseModel):
    name: str
    host: str
    user: str = ""
    port: int = 22
    transport: str = "ssh"
    ssh_pubkey: str = ""
    key_fingerprint: str = ""


class TargetUpdate(BaseModel):
    ssh_pubkey: Optional[str] = None
    key_fingerprint: Optional[str] = None
    last_used_at: Optional[datetime] = None


class LaunchCreate(BaseModel):
    transport: str
    target_id: Optional[str] = None
    target_host: str = ""
    target_namespace: str = ""
    agent_kind: str
    agent_profile: str = ""
    capability_scope: list[str] = []
    runtime_session_id: str = ""
    ttl_hours: int = 8


class HeartbeatUpdate(BaseModel):
    log_tail: str = ""


class CompleteUpdate(BaseModel):
    exit_code: int
    error_message: str = ""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _issue_session_token(
    subject: str,
    ttl_hours: int,
    capability_scope: str,
    db,
) -> tuple[UserSession, str]:
    """Create a scoped UserSession and return (row, raw_token)."""
    raw_token = "mcs_" + secrets.token_hex(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    expires_at = datetime.utcnow() + timedelta(hours=ttl_hours)
    row = UserSession(
        subject=subject,
        token_hash=token_hash,
        token_prefix=raw_token[:8],
        expires_at=expires_at,
        user_agent="mc-remotectl",
        capability_scope=capability_scope,
    )
    db.add(row)
    db.flush()
    return row, raw_token


# ── Target endpoints ──────────────────────────────────────────────────────────

@router.post("/targets", status_code=201)
def create_target(body: TargetCreate, request: Request):
    subject = actor_subject_from_request(request)
    import uuid
    target_id = str(uuid.uuid4())
    now = datetime.utcnow()
    row = RemoteTarget(
        id=target_id,
        owner_subject=subject,
        name=body.name.strip(),
        host=body.host.strip(),
        user=body.user.strip(),
        port=body.port,
        transport=body.transport,
        ssh_pubkey=body.ssh_pubkey,
        key_fingerprint=body.key_fingerprint,
        created_at=now,
        updated_at=now,
    )
    with get_session() as db:
        existing = db.exec(
            select(RemoteTarget)
            .where(RemoteTarget.owner_subject == subject)
            .where(RemoteTarget.name == row.name)
        ).first()
        if existing:
            raise HTTPException(status_code=409, detail=f"Target '{row.name}' already exists")
        db.add(row)
        db.commit()
        db.refresh(row)
        return row


@router.get("/targets")
def list_targets(request: Request):
    subject = actor_subject_from_request(request)
    with get_session() as db:
        rows = db.exec(
            select(RemoteTarget)
            .where(RemoteTarget.owner_subject == subject)
            .order_by(RemoteTarget.created_at.desc())
        ).all()
        return {"targets": rows}


@router.get("/targets/{target_id}")
def get_target(target_id: str, request: Request):
    subject = actor_subject_from_request(request)
    with get_session() as db:
        row = db.get(RemoteTarget, target_id)
        if not row or row.owner_subject != subject:
            raise HTTPException(status_code=404, detail="Target not found")
        return row


@router.patch("/targets/{target_id}")
def update_target(target_id: str, body: TargetUpdate, request: Request):
    subject = actor_subject_from_request(request)
    with get_session() as db:
        row = db.get(RemoteTarget, target_id)
        if not row or row.owner_subject != subject:
            raise HTTPException(status_code=404, detail="Target not found")
        if body.ssh_pubkey is not None:
            row.ssh_pubkey = body.ssh_pubkey
        if body.key_fingerprint is not None:
            row.key_fingerprint = body.key_fingerprint
        if body.last_used_at is not None:
            row.last_used_at = body.last_used_at
        row.updated_at = datetime.utcnow()
        db.add(row)
        db.commit()
        db.refresh(row)
        return row


@router.delete("/targets/{target_id}", status_code=204)
def delete_target(target_id: str, request: Request):
    subject = actor_subject_from_request(request)
    with get_session() as db:
        row = db.get(RemoteTarget, target_id)
        if not row or row.owner_subject != subject:
            raise HTTPException(status_code=404, detail="Target not found")
        db.delete(row)
        db.commit()


# ── Launch endpoints ──────────────────────────────────────────────────────────

@router.post("/launches", status_code=201)
def create_launch(body: LaunchCreate, request: Request):
    subject = actor_subject_from_request(request)
    import uuid
    launch_id = str(uuid.uuid4())
    capability_scope_str = ",".join(s.strip() for s in body.capability_scope if s.strip())
    now = datetime.utcnow()

    with get_session() as db:
        session_row, raw_token = _issue_session_token(
            subject=subject,
            ttl_hours=body.ttl_hours,
            capability_scope=capability_scope_str,
            db=db,
        )

        record = RemoteLaunchRecord(
            id=launch_id,
            owner_subject=subject,
            transport=body.transport,
            target_id=body.target_id,
            target_host=body.target_host,
            target_namespace=body.target_namespace,
            agent_kind=body.agent_kind,
            agent_profile=body.agent_profile,
            runtime_session_id=body.runtime_session_id,
            session_token_id=session_row.id,
            capability_scope=capability_scope_str,
            status="launching",
            created_at=now,
            updated_at=now,
        )
        db.add(record)
        db.commit()
        db.refresh(record)

        result = dict(record)
        result["session_token"] = raw_token  # one-time inclusion
        return result


@router.get("/launches")
def list_launches(request: Request):
    subject = actor_subject_from_request(request)
    with get_session() as db:
        rows = db.exec(
            select(RemoteLaunchRecord)
            .where(RemoteLaunchRecord.owner_subject == subject)
            .order_by(RemoteLaunchRecord.created_at.desc())
        ).all()
        return {"launches": rows}


@router.get("/launches/{launch_id}")
def get_launch(launch_id: str, request: Request):
    subject = actor_subject_from_request(request)
    with get_session() as db:
        row = db.get(RemoteLaunchRecord, launch_id)
        if not row or row.owner_subject != subject:
            raise HTTPException(status_code=404, detail="Launch not found")
        result = dict(row)
        if row.last_heartbeat_at:
            age_seconds = (datetime.utcnow() - row.last_heartbeat_at).total_seconds()
            result["heartbeat_age_seconds"] = int(age_seconds)
        else:
            result["heartbeat_age_seconds"] = None
        return result


@router.post("/launches/{launch_id}/heartbeat")
def heartbeat_launch(launch_id: str, body: HeartbeatUpdate, request: Request):
    """Must be called with the launch's own session token."""
    subject = actor_subject_from_request(request)
    with get_session() as db:
        row = db.get(RemoteLaunchRecord, launch_id)
        if not row:
            raise HTTPException(status_code=404, detail="Launch not found")
        # The token subject must match the launch owner
        if row.owner_subject != subject:
            raise HTTPException(status_code=403, detail="Forbidden")
        now = datetime.utcnow()
        row.last_heartbeat_at = now
        row.updated_at = now
        if row.status == "launching":
            row.status = "running"
        if body.log_tail:
            row.log_tail = body.log_tail[-4096:]  # keep last 4 KiB
        db.add(row)
        db.commit()
        return {"status": row.status, "launch_id": launch_id}


@router.post("/launches/{launch_id}/complete")
def complete_launch(launch_id: str, body: CompleteUpdate, request: Request):
    subject = actor_subject_from_request(request)
    with get_session() as db:
        row = db.get(RemoteLaunchRecord, launch_id)
        if not row or row.owner_subject != subject:
            raise HTTPException(status_code=404, detail="Launch not found")
        row.exit_code = body.exit_code
        row.error_message = body.error_message
        row.status = "completed" if body.exit_code == 0 else "failed"
        row.updated_at = datetime.utcnow()
        # Revoke associated session token
        if row.session_token_id is not None:
            session_row = db.get(UserSession, row.session_token_id)
            if session_row:
                session_row.revoked = True
                db.add(session_row)
        db.add(row)
        db.commit()
        return {"status": row.status, "launch_id": launch_id}


@router.delete("/launches/{launch_id}", status_code=204)
def delete_launch(launch_id: str, request: Request):
    """Revoke the launch token and mark the record as failed."""
    subject = actor_subject_from_request(request)
    with get_session() as db:
        row = db.get(RemoteLaunchRecord, launch_id)
        if not row or row.owner_subject != subject:
            raise HTTPException(status_code=404, detail="Launch not found")
        row.status = "failed"
        row.error_message = "killed by owner"
        row.updated_at = datetime.utcnow()
        if row.session_token_id is not None:
            session_row = db.get(UserSession, row.session_token_id)
            if session_row:
                session_row.revoked = True
                db.add(session_row)
        db.add(row)
        db.commit()
