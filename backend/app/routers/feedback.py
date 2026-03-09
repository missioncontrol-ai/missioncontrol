import json
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from sqlmodel import select

from app.db import get_session
from app.models import FeedbackEntry
from app.schemas import FeedbackCreate, FeedbackRead, FeedbackSummaryRead, FeedbackTriageUpdate
from app.services.authz import (
    actor_subject_from_request,
    assert_mission_reader_or_admin,
    assert_mission_writer_or_admin,
)

router = APIRouter(prefix="/feedback", tags=["feedback"])


def _serialize_feedback(row: FeedbackEntry) -> dict:
    metadata = {}
    if row.metadata_json:
        try:
            parsed = json.loads(row.metadata_json)
            if isinstance(parsed, dict):
                metadata = parsed
        except Exception:
            metadata = {}
    return {
        "id": row.id,
        "mission_id": row.mission_id,
        "kluster_id": row.kluster_id,
        "source_type": row.source_type,
        "source_subject": row.source_subject,
        "provider": row.provider,
        "channel_id": row.channel_id,
        "category": row.category,
        "severity": row.severity,
        "summary": row.summary,
        "recommendation": row.recommendation,
        "status": row.status,
        "triage_status": row.triage_status,
        "priority": row.priority,
        "owner": row.owner,
        "disposition": row.disposition,
        "outcome_ref": row.outcome_ref,
        "metadata": metadata,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _create_feedback(source_type: str, payload: FeedbackCreate, request: Request) -> dict:
    with get_session() as session:
        assert_mission_writer_or_admin(session=session, request=request, mission_id=payload.mission_id)
        now = datetime.utcnow()
        row = FeedbackEntry(
            mission_id=payload.mission_id,
            kluster_id=payload.kluster_id or "",
            source_type=source_type,
            source_subject=actor_subject_from_request(request),
            provider=(payload.provider or "").strip(),
            channel_id=(payload.channel_id or "").strip(),
            category=(payload.category or "").strip(),
            severity=(payload.severity or "medium").strip().lower() or "medium",
            summary=payload.summary.strip(),
            recommendation=(payload.recommendation or "").strip(),
            status="open",
            triage_status="new",
            priority="p2",
            metadata_json=json.dumps(payload.metadata or {}, separators=(",", ":")),
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return _serialize_feedback(row)


@router.post("/agent", response_model=FeedbackRead)
def create_agent_feedback(payload: FeedbackCreate, request: Request):
    return _create_feedback("agent", payload, request)


@router.post("/human", response_model=FeedbackRead)
def create_human_feedback(payload: FeedbackCreate, request: Request):
    return _create_feedback("human", payload, request)


@router.get("", response_model=list[FeedbackRead])
def list_feedback(
    mission_id: str,
    request: Request,
    triage_status: str = "",
    source_type: str = "",
    priority: str = "",
    limit: int = 200,
):
    with get_session() as session:
        assert_mission_reader_or_admin(session=session, request=request, mission_id=mission_id)
        query = select(FeedbackEntry).where(FeedbackEntry.mission_id == mission_id)
        if triage_status:
            query = query.where(FeedbackEntry.triage_status == triage_status.strip().lower())
        if source_type:
            query = query.where(FeedbackEntry.source_type == source_type.strip().lower())
        if priority:
            query = query.where(FeedbackEntry.priority == priority.strip().lower())
        bounded_limit = max(1, min(limit, 1000))
        query = query.order_by(FeedbackEntry.updated_at.desc()).limit(bounded_limit)
        rows = session.exec(query).all()
        return [_serialize_feedback(row) for row in rows]


@router.patch("/{feedback_id}/triage", response_model=FeedbackRead)
def update_feedback_triage(feedback_id: int, payload: FeedbackTriageUpdate, request: Request):
    with get_session() as session:
        row = session.get(FeedbackEntry, feedback_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Feedback entry not found")
        assert_mission_writer_or_admin(session=session, request=request, mission_id=row.mission_id)
        now = datetime.utcnow()
        if payload.triage_status is not None:
            row.triage_status = payload.triage_status.strip().lower()
        if payload.priority is not None:
            row.priority = payload.priority.strip().lower()
        if payload.owner is not None:
            row.owner = payload.owner.strip()
        if payload.disposition is not None:
            row.disposition = payload.disposition.strip().lower()
        if payload.outcome_ref is not None:
            row.outcome_ref = payload.outcome_ref.strip()
        row.updated_at = now
        session.add(row)
        session.commit()
        session.refresh(row)
        return _serialize_feedback(row)


@router.get("/summary", response_model=FeedbackSummaryRead)
def feedback_summary(mission_id: str, request: Request):
    with get_session() as session:
        assert_mission_reader_or_admin(session=session, request=request, mission_id=mission_id)
        rows = session.exec(select(FeedbackEntry).where(FeedbackEntry.mission_id == mission_id)).all()
        by_source_type: dict[str, int] = {}
        by_severity: dict[str, int] = {}
        by_category: dict[str, int] = {}
        by_triage_status: dict[str, int] = {}
        by_priority: dict[str, int] = {}
        for row in rows:
            by_source_type[row.source_type] = by_source_type.get(row.source_type, 0) + 1
            by_severity[row.severity] = by_severity.get(row.severity, 0) + 1
            category = row.category or "uncategorized"
            by_category[category] = by_category.get(category, 0) + 1
            triage_status = row.triage_status or "new"
            by_triage_status[triage_status] = by_triage_status.get(triage_status, 0) + 1
            priority = row.priority or "p2"
            by_priority[priority] = by_priority.get(priority, 0) + 1
        return {
            "mission_id": mission_id,
            "total": len(rows),
            "by_source_type": by_source_type,
            "by_severity": by_severity,
            "by_category": by_category,
            "by_triage_status": by_triage_status,
            "by_priority": by_priority,
        }
