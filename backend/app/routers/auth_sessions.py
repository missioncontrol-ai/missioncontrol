"""Session-scoped token management.

POST  /auth/sessions          — exchange current credentials for a session token
POST  /auth/sessions/refresh  — extend an active session's TTL
DELETE /auth/sessions/current — revoke the active session
GET   /auth/me                — identity of the current principal
"""

import hashlib
import os
import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlmodel import select

from app.db import get_session
from app.models import UserSession
from app.services.authz import actor_subject_from_request

router = APIRouter(tags=["auth"])

SESSION_TOKEN_PREFIX = "mcs_"
_DEFAULT_TTL_HOURS = 8
_MAX_TTL_HOURS = 720  # 30 days hard cap


def _ttl_hours() -> int:
    try:
        return max(1, min(_MAX_TTL_HOURS, int(os.getenv("MC_SESSION_TTL_HOURS", str(_DEFAULT_TTL_HOURS)))))
    except ValueError:
        return _DEFAULT_TTL_HOURS


def _make_token() -> str:
    return SESSION_TOKEN_PREFIX + secrets.token_urlsafe(32)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class SessionCreateRequest(BaseModel):
    ttl_hours: int = _DEFAULT_TTL_HOURS


class SessionResponse(BaseModel):
    token: str
    subject: str
    expires_at: datetime
    session_id: int
    ttl_hours: int


class MeResponse(BaseModel):
    subject: str
    email: str | None
    auth_type: str
    session_id: int | None = None
    session_expires_at: datetime | None = None


@router.post("/auth/sessions", response_model=SessionResponse)
def create_session(payload: SessionCreateRequest, request: Request):
    subject = actor_subject_from_request(request)
    ttl = max(1, min(_MAX_TTL_HOURS, payload.ttl_hours or _ttl_hours()))
    token = _make_token()
    token_hash = _hash_token(token)
    expires_at = datetime.utcnow() + timedelta(hours=ttl)
    user_agent = request.headers.get("user-agent", "")[:512]

    session_row = UserSession(
        subject=subject,
        token_hash=token_hash,
        token_prefix=token[:12],
        expires_at=expires_at,
        user_agent=user_agent,
    )
    with get_session() as db:
        db.add(session_row)
        db.commit()
        db.refresh(session_row)
        session_id = session_row.id

    return SessionResponse(
        token=token,
        subject=subject,
        expires_at=expires_at,
        session_id=session_id,
        ttl_hours=ttl,
    )


@router.post("/auth/sessions/refresh", response_model=SessionResponse)
def refresh_session(request: Request):
    """Extend the TTL of the current session token. Only works for mcs_ tokens."""
    principal = getattr(request.state, "principal", None)
    if not isinstance(principal, dict) or principal.get("auth_type") != "session":
        raise HTTPException(status_code=400, detail="Only session tokens (mcs_*) can be refreshed")

    session_id = principal.get("session_id")
    subject = actor_subject_from_request(request)
    ttl = _ttl_hours()
    new_expires_at = datetime.utcnow() + timedelta(hours=ttl)

    with get_session() as db:
        session_row = db.exec(
            select(UserSession)
            .where(UserSession.id == session_id)
            .where(UserSession.revoked == False)
        ).first()
        if session_row is None:
            raise HTTPException(status_code=404, detail="Session not found or already revoked")
        session_row.expires_at = new_expires_at
        session_row.last_used_at = datetime.utcnow()
        db.add(session_row)
        db.commit()

    return SessionResponse(
        token="(not re-issued on refresh)",
        subject=subject,
        expires_at=new_expires_at,
        session_id=session_id,
        ttl_hours=ttl,
    )


@router.delete("/auth/sessions/current", status_code=204)
def revoke_session(request: Request):
    """Revoke the current session token. No-op for non-session auth."""
    principal = getattr(request.state, "principal", None)
    if not isinstance(principal, dict) or principal.get("auth_type") != "session":
        # Non-session auth: nothing to revoke, succeed silently
        return

    session_id = principal.get("session_id")
    with get_session() as db:
        session_row = db.exec(
            select(UserSession).where(UserSession.id == session_id)
        ).first()
        if session_row is not None:
            session_row.revoked = True
            db.add(session_row)
            db.commit()


@router.get("/auth/me", response_model=MeResponse)
def whoami(request: Request):
    principal = getattr(request.state, "principal", {})
    return MeResponse(
        subject=str(principal.get("subject") or ""),
        email=principal.get("email"),
        auth_type=str(principal.get("auth_type") or "unknown"),
        session_id=principal.get("session_id"),
        session_expires_at=principal.get("session_expires_at"),
    )
