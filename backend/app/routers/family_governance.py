"""Family governance router — per-user age profiles, access windows, token caps."""
import json
import uuid
from datetime import datetime, time
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlmodel import select

from app.db import get_session
from app.models import FamilyMember
from app.services.authz import actor_subject_from_request, ensure_platform_admin

router = APIRouter(prefix="/family", tags=["family-governance"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class FamilyMemberCreate(BaseModel):
    subject: str
    display_name: str
    age_group: str
    goose_mode: str = "chat"
    model_allowlist: list[str] = []
    token_daily_cap: Optional[int] = None
    allowed_hours_start: Optional[str] = None  # "HH:MM"
    allowed_hours_end: Optional[str] = None    # "HH:MM"
    allowed_hours_tz: str = "America/New_York"
    next_review_date: Optional[str] = None


class FamilyMemberUpdate(BaseModel):
    display_name: Optional[str] = None
    age_group: Optional[str] = None
    goose_mode: Optional[str] = None
    model_allowlist: Optional[list[str]] = None
    token_daily_cap: Optional[int] = None
    allowed_hours_start: Optional[str] = None
    allowed_hours_end: Optional[str] = None
    allowed_hours_tz: Optional[str] = None
    next_review_date: Optional[str] = None


class FamilyMemberRead(BaseModel):
    id: str
    subject: str
    display_name: str
    age_group: str
    goose_mode: str
    model_allowlist: list[str]
    token_daily_cap: Optional[int]
    allowed_hours_start: Optional[str]
    allowed_hours_end: Optional[str]
    allowed_hours_tz: str
    next_review_date: Optional[str]
    created_at: datetime
    updated_at: datetime


class AccessCheckResult(BaseModel):
    allowed: bool
    reason: str
    resets_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _serialize(row: FamilyMember) -> FamilyMemberRead:
    return FamilyMemberRead(
        id=row.id,
        subject=row.subject,
        display_name=row.display_name,
        age_group=row.age_group,
        goose_mode=row.goose_mode,
        model_allowlist=json.loads(row.model_allowlist_json),
        token_daily_cap=row.token_daily_cap,
        allowed_hours_start=row.allowed_hours_start,
        allowed_hours_end=row.allowed_hours_end,
        allowed_hours_tz=row.allowed_hours_tz,
        next_review_date=row.next_review_date,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _check_time_window(member: FamilyMember) -> tuple[bool, str]:
    if not member.allowed_hours_start or not member.allowed_hours_end:
        return True, "no time restriction"
    try:
        tz = ZoneInfo(member.allowed_hours_tz)
        now = datetime.now(tz).time()
        start = time.fromisoformat(member.allowed_hours_start)
        end = time.fromisoformat(member.allowed_hours_end)
        if start <= now <= end:
            return True, "within allowed hours"
        return False, f"outside allowed hours ({member.allowed_hours_start}–{member.allowed_hours_end} {member.allowed_hours_tz})"
    except Exception:
        return True, "time check skipped (parse error)"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/members", response_model=list[FamilyMemberRead])
def list_members(request: Request):
    ensure_platform_admin(request)
    with get_session() as session:
        rows = session.exec(select(FamilyMember)).all()
        return [_serialize(r) for r in rows]


@router.get("/members/{subject}", response_model=FamilyMemberRead)
def get_member(subject: str, request: Request):
    actor = actor_subject_from_request(request)
    with get_session() as session:
        row = session.exec(select(FamilyMember).where(FamilyMember.subject == subject)).first()
        if not row:
            raise HTTPException(status_code=404, detail="member not found")
        # Allow self-read; admins can read anyone
        if actor != subject:
            ensure_platform_admin(request)
        return _serialize(row)


@router.post("/members", response_model=FamilyMemberRead, status_code=201)
def create_member(payload: FamilyMemberCreate, request: Request):
    ensure_platform_admin(request)
    with get_session() as session:
        existing = session.exec(select(FamilyMember).where(FamilyMember.subject == payload.subject)).first()
        if existing:
            raise HTTPException(status_code=409, detail="member already exists")
        now = datetime.utcnow()
        row = FamilyMember(
            id=str(uuid.uuid4()),
            subject=payload.subject,
            display_name=payload.display_name,
            age_group=payload.age_group,
            goose_mode=payload.goose_mode,
            model_allowlist_json=json.dumps(payload.model_allowlist),
            token_daily_cap=payload.token_daily_cap,
            allowed_hours_start=payload.allowed_hours_start,
            allowed_hours_end=payload.allowed_hours_end,
            allowed_hours_tz=payload.allowed_hours_tz,
            next_review_date=payload.next_review_date,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return _serialize(row)


@router.put("/members/{subject}", response_model=FamilyMemberRead)
def update_member(subject: str, payload: FamilyMemberUpdate, request: Request):
    ensure_platform_admin(request)
    with get_session() as session:
        row = session.exec(select(FamilyMember).where(FamilyMember.subject == subject)).first()
        if not row:
            raise HTTPException(status_code=404, detail="member not found")
        if payload.display_name is not None:
            row.display_name = payload.display_name
        if payload.age_group is not None:
            row.age_group = payload.age_group
        if payload.goose_mode is not None:
            row.goose_mode = payload.goose_mode
        if payload.model_allowlist is not None:
            row.model_allowlist_json = json.dumps(payload.model_allowlist)
        if payload.token_daily_cap is not None:
            row.token_daily_cap = payload.token_daily_cap
        if payload.allowed_hours_start is not None:
            row.allowed_hours_start = payload.allowed_hours_start
        if payload.allowed_hours_end is not None:
            row.allowed_hours_end = payload.allowed_hours_end
        if payload.allowed_hours_tz is not None:
            row.allowed_hours_tz = payload.allowed_hours_tz
        if payload.next_review_date is not None:
            row.next_review_date = payload.next_review_date
        row.updated_at = datetime.utcnow()
        session.add(row)
        session.commit()
        session.refresh(row)
        return _serialize(row)


@router.get("/members/{subject}/access", response_model=AccessCheckResult)
def check_access(subject: str, request: Request):
    """Check whether a family member is currently allowed to start a session."""
    with get_session() as session:
        row = session.exec(select(FamilyMember).where(FamilyMember.subject == subject)).first()
        if not row:
            raise HTTPException(status_code=404, detail="member not found")

        allowed, reason = _check_time_window(row)
        if not allowed:
            return AccessCheckResult(allowed=False, reason=reason)

        return AccessCheckResult(allowed=True, reason=reason)
