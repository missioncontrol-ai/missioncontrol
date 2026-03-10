from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from sqlmodel import select
from app.db import get_session
from app.models import AgentMessage, Kluster, OverlapSuggestion, Task, TaskAssignment
from app.schemas import TaskCreate, TaskRead, TaskUpdate, OverlapSuggestionRead
from app.services.authz import assert_mission_owner_or_admin, assert_mission_reader_or_admin, assert_mission_writer_or_admin
from app.services.overlap import score_overlap, score_overlap_vector, task_text
from app.services.vectorstore import index_task
from app.services.git_ledger import (
    enqueue_ledger_event,
    actor_subject_from_request,
    request_source,
    try_publish_bootstrap_event,
)
from app.services.schema_pack import enforce_schema_pack
from app.services.governance import extract_approval_context, require_policy_action
from app.services.task_identity import ensure_task_public_id, resolve_task_by_ref
from app.services.pagination import bounded_limit, limit_query

router = APIRouter(prefix="/missions/{mission_id}/k/{kluster_id}/t", tags=["tasks"])


@router.post("", response_model=TaskRead)
def create_task(mission_id: str, kluster_id: str, payload: TaskCreate, request: Request):
    with get_session() as session:
        require_policy_action(
            session=session,
            action="task.create",
            request=request,
            approval_context=extract_approval_context(request),
            channel="api",
        )
        assert_mission_writer_or_admin(session=session, request=request, mission_id=mission_id)
        kluster = session.get(Kluster, kluster_id)
        if not kluster or kluster.mission_id != mission_id:
            raise HTTPException(status_code=404, detail="Kluster not found")
        if payload.kluster_id != kluster_id:
            raise HTTPException(status_code=409, detail="Payload kluster_id must match route kluster_id")
        task_data = enforce_schema_pack(
            request=request,
            entity_type="task",
            payload=payload.dict(),
            operation="create",
        )
        task_data["kluster_id"] = kluster_id
        task = Task(**task_data)
        session.add(task)
        session.commit()
        session.refresh(task)
        ensure_task_public_id(session, task)

        index_task(
            task.id,
            task_text(task),
            {"kluster_id": task.kluster_id, "status": task.status},
        )

        candidates = session.exec(
            select(Task)
            .where(Task.id != task.id)
            .where(Task.kluster_id == kluster_id)
            .order_by(Task.updated_at.desc())
            .limit(200)
        ).all()
        overlaps = score_overlap(task, candidates)
        for cand, score, evidence in overlaps[:5]:
            suggestion = OverlapSuggestion(
                task_id=task.id,
                candidate_task_id=cand.id,
                similarity_score=score,
                evidence=evidence,
                suggested_action="link",
            )
            session.add(suggestion)

        vector_matches = score_overlap_vector(task)
        for cand_id, dist in vector_matches:
            suggestion = OverlapSuggestion(
                task_id=task.id,
                candidate_task_id=cand_id,
                similarity_score=float(100.0 - dist),
                evidence=f"Vector similarity distance {dist:.3f}",
                suggested_action="link",
            )
            session.add(suggestion)

        session.commit()
        session.refresh(task)
        event = enqueue_ledger_event(
            session=session,
            mission_id=mission_id,
            kluster_id=task.kluster_id,
            entity_type="task",
            entity_id=task.public_id or str(task.id),
            action="create",
            before=None,
            after=task.dict(),
            actor_subject=actor_subject_from_request(request),
            source=request_source(request),
            pending_uncommitted_changes=True,
        )
        try_publish_bootstrap_event(
            session=session,
            event_id=event.id,
            actor_subject=actor_subject_from_request(request),
        )
        session.refresh(task)

        return task


@router.get("", response_model=list[TaskRead])
def list_tasks(
    mission_id: str,
    kluster_id: str,
    status: Optional[str] = None,
    request: Request = None,
    limit: int = limit_query(),
):
    with get_session() as session:
        kluster = session.get(Kluster, kluster_id)
        if not kluster or kluster.mission_id != mission_id:
            raise HTTPException(status_code=404, detail="Kluster not found")
        assert_mission_reader_or_admin(session=session, request=request, mission_id=mission_id)
        stmt = select(Task).where(Task.kluster_id == kluster_id)
        if status is not None:
            stmt = stmt.where(Task.status == status)
        tasks = session.exec(stmt.order_by(Task.updated_at.desc()).limit(bounded_limit(limit))).all()
        for task in tasks:
            ensure_task_public_id(session, task)
        return tasks


@router.get("/{task_id}", response_model=TaskRead)
def get_task(mission_id: str, kluster_id: str, task_id: str, request: Request = None):
    with get_session() as session:
        kluster = session.get(Kluster, kluster_id)
        if not kluster or kluster.mission_id != mission_id:
            raise HTTPException(status_code=404, detail="Kluster not found")
        assert_mission_reader_or_admin(session=session, request=request, mission_id=mission_id)
        task = resolve_task_by_ref(session=session, task_ref=task_id, kluster_id=kluster_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        ensure_task_public_id(session, task)
        return task


@router.patch("/{task_id}", response_model=TaskRead)
def update_task(mission_id: str, kluster_id: str, task_id: str, payload: TaskUpdate, request: Request):
    with get_session() as session:
        require_policy_action(
            session=session,
            action="task.update",
            request=request,
            approval_context=extract_approval_context(request),
            channel="api",
        )
        kluster = session.get(Kluster, kluster_id)
        if not kluster or kluster.mission_id != mission_id:
            raise HTTPException(status_code=404, detail="Kluster not found")
        assert_mission_writer_or_admin(session=session, request=request, mission_id=mission_id)
        task = resolve_task_by_ref(session=session, task_ref=task_id, kluster_id=kluster_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        before = task.dict()
        updates = enforce_schema_pack(
            request=request,
            entity_type="task",
            payload=payload.dict(exclude_unset=True),
            operation="update",
        )
        updates["kluster_id"] = kluster_id
        for k, v in updates.items():
            setattr(task, k, v)
        task.updated_at = datetime.utcnow()
        session.add(task)
        session.commit()
        session.refresh(task)
        ensure_task_public_id(session, task)

        index_task(
            task.id,
            task_text(task),
            {"kluster_id": task.kluster_id, "status": task.status},
        )
        enqueue_ledger_event(
            session=session,
            mission_id=mission_id,
            kluster_id=task.kluster_id,
            entity_type="task",
            entity_id=task.public_id or str(task.id),
            action="update",
            before=before,
            after=task.dict(),
            actor_subject=actor_subject_from_request(request),
            source=request_source(request),
        )
        session.refresh(task)
        return task


@router.get("/{task_id}/overlaps", response_model=list[OverlapSuggestionRead])
def list_overlaps(mission_id: str, kluster_id: str, task_id: str, limit: int = limit_query(default=20, maximum=100)):
    with get_session() as session:
        kluster = session.get(Kluster, kluster_id)
        if not kluster or kluster.mission_id != mission_id:
            raise HTTPException(status_code=404, detail="Kluster not found")
        task = resolve_task_by_ref(session=session, task_ref=task_id, kluster_id=kluster_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        suggestions = session.exec(
            select(OverlapSuggestion).where(OverlapSuggestion.task_id == task.id).limit(bounded_limit(limit, default=20, maximum=100))
        ).all()
        return suggestions


@router.delete("/{task_id}")
def delete_task(mission_id: str, kluster_id: str, task_id: str, request: Request):
    with get_session() as session:
        require_policy_action(
            session=session,
            action="task.delete",
            request=request,
            approval_context=extract_approval_context(request),
            channel="api",
        )
        kluster = session.get(Kluster, kluster_id)
        if not kluster or kluster.mission_id != mission_id:
            raise HTTPException(status_code=404, detail="Kluster not found")
        assert_mission_owner_or_admin(session=session, request=request, mission_id=mission_id)
        task = resolve_task_by_ref(session=session, task_ref=task_id, kluster_id=kluster_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        internal_task_id = task.id

        before = task.dict()
        overlaps = session.exec(
            select(OverlapSuggestion).where(
                (OverlapSuggestion.task_id == internal_task_id) | (OverlapSuggestion.candidate_task_id == internal_task_id)
            )
        ).all()
        for overlap in overlaps:
            session.delete(overlap)
        assignments = session.exec(select(TaskAssignment).where(TaskAssignment.task_id == internal_task_id)).all()
        for assignment in assignments:
            session.delete(assignment)
        messages = session.exec(select(AgentMessage).where(AgentMessage.task_id == internal_task_id)).all()
        for message in messages:
            message.task_id = None
            session.add(message)
        session.delete(task)
        session.commit()
        enqueue_ledger_event(
            session=session,
            mission_id=mission_id,
            kluster_id=task.kluster_id,
            entity_type="task",
            entity_id=task.public_id or str(internal_task_id),
            action="delete",
            before=before,
            after=None,
            actor_subject=actor_subject_from_request(request),
            source=request_source(request),
        )
        return {"ok": True, "deleted_id": task.public_id or str(internal_task_id)}
