import json
import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Request
from sqlmodel import select

from app.db import get_session
from app.models import ApprovalRequest
from app.schemas import (
    ApprovalDecisionRead,
    ApprovalRequestCreate,
    ApprovalRequestDecision,
    ApprovalRequestRead,
)
from app.services.authz import (
    actor_subject_from_request,
    assert_mission_owner_or_admin,
    assert_mission_reader_or_admin,
    assert_mission_writer_or_admin,
)
from app.services.governance import generate_approval_token
from app.services.notifications import emit_controlplane_event
from app.services.pagination import bounded_limit, limit_query

router = APIRouter(prefix="/approvals", tags=["approvals"])


def _serialize_approval(row: ApprovalRequest) -> dict:
    context = {}
    if row.request_context_json:
        try:
            parsed = json.loads(row.request_context_json)
            if isinstance(parsed, dict):
                context = parsed
        except Exception:
            context = {}
    return {
        "id": row.id,
        "mission_id": row.mission_id,
        "action": row.action,
        "channel": row.channel,
        "reason": row.reason,
        "target_entity_type": row.target_entity_type,
        "target_entity_id": row.target_entity_id,
        "request_context": context,
        "status": row.status,
        "requested_by": row.requested_by,
        "approved_by": row.approved_by,
        "rejected_by": row.rejected_by,
        "decision_note": row.decision_note,
        "approval_expires_at": row.approval_expires_at,
        "approved_at": row.approved_at,
        "rejected_at": row.rejected_at,
        "executed_at": row.executed_at,
        "executed_action": row.executed_action,
        "executed_request_id": row.executed_request_id,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


@router.post("/requests", response_model=ApprovalRequestRead)
def create_approval_request(payload: ApprovalRequestCreate, request: Request):
    with get_session() as session:
        assert_mission_writer_or_admin(session=session, request=request, mission_id=payload.mission_id)
        expires_seconds = max(60, min(int(payload.expires_in_seconds or 900), 86400))
        row = ApprovalRequest(
            mission_id=payload.mission_id,
            action=payload.action.strip(),
            channel=(payload.channel or "api").strip() or "api",
            reason=payload.reason or "",
            target_entity_type=payload.target_entity_type or "",
            target_entity_id=payload.target_entity_id or "",
            request_context_json=json.dumps(payload.request_context or {}, separators=(",", ":")),
            status="pending",
            requested_by=actor_subject_from_request(request),
            approval_expires_at=datetime.utcnow() + timedelta(seconds=expires_seconds),
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        emit_controlplane_event(
            req=request,
            event_type="approval.requested",
            payload={
                "approval_request_id": row.id,
                "mission_id": row.mission_id,
                "action": row.action,
                "requested_by": row.requested_by,
                "channel": row.channel,
            },
        )
        return _serialize_approval(row)


@router.get("", response_model=list[ApprovalRequestRead])
def list_approval_requests(
    request: Request,
    mission_id: str,
    status: str | None = None,
    limit: int = limit_query(default=50, maximum=100),
):
    with get_session() as session:
        assert_mission_reader_or_admin(session=session, request=request, mission_id=mission_id)
        stmt = select(ApprovalRequest).where(ApprovalRequest.mission_id == mission_id)
        if status:
            stmt = stmt.where(ApprovalRequest.status == status)
        rows = session.exec(stmt.order_by(ApprovalRequest.created_at.desc()).limit(bounded_limit(limit, default=50, maximum=100))).all()
        return [_serialize_approval(row) for row in rows]


@router.post("/{approval_id}/approve", response_model=ApprovalDecisionRead)
def approve_request(approval_id: int, payload: ApprovalRequestDecision, request: Request):
    with get_session() as session:
        row = session.get(ApprovalRequest, approval_id)
        if not row:
            raise HTTPException(status_code=404, detail="Approval request not found")
        assert_mission_owner_or_admin(session=session, request=request, mission_id=row.mission_id)
        if row.status != "pending":
            raise HTTPException(status_code=409, detail="Only pending requests can be approved")

        expires_seconds = max(60, min(int(payload.expires_in_seconds or 900), 86400))
        now = datetime.utcnow()
        nonce = secrets.token_urlsafe(16)
        row.status = "approved"
        row.approved_by = actor_subject_from_request(request)
        row.approved_at = now
        row.decision_note = payload.note or ""
        row.approval_nonce = nonce
        row.approval_expires_at = now + timedelta(seconds=expires_seconds)
        row.updated_at = now
        session.add(row)
        session.commit()
        session.refresh(row)

        token_payload = {
            "approval_request_id": row.id,
            "mission_id": row.mission_id,
            "action": row.action,
            "request_id": f"approval-{row.id}",
            "approved_by": row.approved_by,
            "approved_at": row.approved_at.isoformat(),
            "nonce": row.approval_nonce,
            "exp": int(row.approval_expires_at.timestamp()) if row.approval_expires_at else int(now.timestamp()) + 900,
        }
        approval_token = generate_approval_token(token_payload)
        emit_controlplane_event(
            req=request,
            event_type="approval.approved",
            payload={
                "approval_request_id": row.id,
                "mission_id": row.mission_id,
                "action": row.action,
                "approved_by": row.approved_by,
                "expires_at": row.approval_expires_at.isoformat() if row.approval_expires_at else "",
            },
        )
        return {"approval": _serialize_approval(row), "approval_token": approval_token}


@router.post("/{approval_id}/reject", response_model=ApprovalRequestRead)
def reject_request(approval_id: int, payload: ApprovalRequestDecision, request: Request):
    with get_session() as session:
        row = session.get(ApprovalRequest, approval_id)
        if not row:
            raise HTTPException(status_code=404, detail="Approval request not found")
        assert_mission_owner_or_admin(session=session, request=request, mission_id=row.mission_id)
        if row.status != "pending":
            raise HTTPException(status_code=409, detail="Only pending requests can be rejected")
        now = datetime.utcnow()
        row.status = "rejected"
        row.rejected_by = actor_subject_from_request(request)
        row.rejected_at = now
        row.decision_note = payload.note or ""
        row.updated_at = now
        session.add(row)
        session.commit()
        session.refresh(row)
        emit_controlplane_event(
            req=request,
            event_type="approval.rejected",
            payload={
                "approval_request_id": row.id,
                "mission_id": row.mission_id,
                "action": row.action,
                "rejected_by": row.rejected_by,
            },
        )
        return _serialize_approval(row)
