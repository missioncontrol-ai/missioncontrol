from fastapi import APIRouter, Request

from app.db import get_session
from app.models import GovernancePolicy
from app.schemas import (
    GovernancePolicyDraftCreate,
    GovernancePolicyDraftUpdate,
    GovernancePolicyEventRead,
    GovernancePolicyPublish,
    GovernancePolicyRead,
    GovernancePolicyRollback,
)
from app.services.authz import actor_subject_from_request
from app.services.governance import (
    create_policy_draft,
    ensure_governance_policy_seed,
    ensure_platform_admin,
    get_active_policy_row,
    list_policy_events,
    list_policy_versions,
    publish_policy_draft,
    rollback_policy,
    serialize_event_row,
    serialize_policy_row,
    update_policy_draft,
    validate_policy,
)

router = APIRouter(prefix="/governance", tags=["governance"])


@router.get("/policy/active", response_model=GovernancePolicyRead)
def get_active_policy(request: Request):
    with get_session() as session:
        ensure_governance_policy_seed(session)
        row = get_active_policy_row(session)
        return serialize_policy_row(row, effective=True)


@router.get("/policy/versions", response_model=list[GovernancePolicyRead])
def get_policy_versions(request: Request):
    with get_session() as session:
        ensure_platform_admin(request)
        ensure_governance_policy_seed(session)
        rows = list_policy_versions(session)
        return [serialize_policy_row(row, effective=False) for row in rows]


@router.post("/policy/drafts", response_model=GovernancePolicyRead)
def create_draft(payload: GovernancePolicyDraftCreate, request: Request):
    with get_session() as session:
        ensure_platform_admin(request)
        row = create_policy_draft(
            session=session,
            actor_subject=actor_subject_from_request(request),
            policy=payload.policy,
            note=payload.change_note,
        )
        return serialize_policy_row(row, effective=False)


@router.patch("/policy/drafts/{draft_id}", response_model=GovernancePolicyRead)
def update_draft(draft_id: int, payload: GovernancePolicyDraftUpdate, request: Request):
    with get_session() as session:
        ensure_platform_admin(request)
        row = update_policy_draft(
            session=session,
            draft_id=draft_id,
            actor_subject=actor_subject_from_request(request),
            policy=payload.policy,
            note=payload.change_note,
        )
        return serialize_policy_row(row, effective=False)


@router.post("/policy/drafts/{draft_id}/validate")
def validate_draft(draft_id: int, request: Request):
    with get_session() as session:
        ensure_platform_admin(request)
        draft = session.get(GovernancePolicy, draft_id)
        if not draft:
            return {"ok": False, "error": "draft_not_found"}
        validate_policy(serialize_policy_row(draft)["policy"])
        return {"ok": True}


@router.post("/policy/drafts/{draft_id}/publish", response_model=GovernancePolicyRead)
def publish_draft(draft_id: int, payload: GovernancePolicyPublish, request: Request):
    with get_session() as session:
        ensure_platform_admin(request)
        row = publish_policy_draft(
            session=session,
            draft_id=draft_id,
            actor_subject=actor_subject_from_request(request),
            note=payload.change_note,
        )
        return serialize_policy_row(row, effective=True)


@router.post("/policy/rollback", response_model=GovernancePolicyRead)
def rollback(payload: GovernancePolicyRollback, request: Request):
    with get_session() as session:
        ensure_platform_admin(request)
        row = rollback_policy(
            session=session,
            version=payload.version,
            actor_subject=actor_subject_from_request(request),
            note=payload.change_note,
        )
        return serialize_policy_row(row, effective=True)


@router.post("/policy/reload")
def reload_policy(request: Request):
    with get_session() as session:
        ensure_platform_admin(request)
        ensure_governance_policy_seed(session)
        return {"ok": True}


@router.get("/policy/events", response_model=list[GovernancePolicyEventRead])
def get_events(request: Request, limit: int = 100):
    with get_session() as session:
        ensure_platform_admin(request)
        rows = list_policy_events(session, limit=max(1, min(limit, 500)))
        return [serialize_event_row(row) for row in rows]
