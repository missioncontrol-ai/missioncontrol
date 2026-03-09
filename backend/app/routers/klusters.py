from datetime import datetime
from fastapi import APIRouter, HTTPException, Request
from sqlmodel import select
from app.db import get_session
from app.models import Artifact, Kluster, Doc, Epic, IngestionJob, Mission, Task
from app.schemas import KlusterCreate, KlusterRead
from app.services.authz import (
    assert_mission_owner_or_admin,
    assert_mission_reader_or_admin,
    assert_mission_writer_or_admin,
)
from app.services.git_ledger import (
    enqueue_ledger_event,
    actor_subject_from_request,
    request_source,
    try_publish_bootstrap_event,
)
from app.services.ids import new_hash_id
from app.services.schema_pack import enforce_schema_pack
from app.services.governance import extract_approval_context, require_policy_action
from app.services.keystone import ensure_kluster_workstream

router = APIRouter(prefix="/missions/{mission_id}/k", tags=["klusters"])


def _owner_list(value: str | None) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


@router.post("", response_model=KlusterRead)
def create_kluster(mission_id: str, payload: KlusterCreate, request: Request):
    with get_session() as session:
        require_policy_action(
            session=session,
            action="kluster.create",
            request=request,
            approval_context=extract_approval_context(request),
            channel="api",
        )
        mission = session.get(Mission, mission_id)
        if not mission:
            raise HTTPException(status_code=404, detail="Mission not found")
        if payload.mission_id is not None and payload.mission_id != mission_id:
            raise HTTPException(status_code=409, detail="Payload mission_id must match route mission_id")
        kluster_data = enforce_schema_pack(
            request=request,
            entity_type="kluster",
            payload=payload.dict(),
            operation="create",
        )
        if not _owner_list(kluster_data.get("owners")):
            raise HTTPException(status_code=422, detail="owners must include at least one owner")
        kluster_data["mission_id"] = mission_id
        kluster = Kluster(id=new_hash_id(), **kluster_data)
        while session.get(Kluster, kluster.id):
            kluster.id = new_hash_id()
        ensure_kluster_workstream(kluster, actor=actor_subject_from_request(request))
        session.add(kluster)
        session.commit()
        session.refresh(kluster)
        event = enqueue_ledger_event(
            session=session,
            mission_id=kluster.mission_id,
            kluster_id=kluster.id,
            entity_type="kluster",
            entity_id=kluster.id,
            action="create",
            before=None,
            after=kluster.dict(),
            actor_subject=actor_subject_from_request(request),
            source=request_source(request),
            pending_uncommitted_changes=True,
        )
        try_publish_bootstrap_event(
            session=session,
            event_id=event.id,
            actor_subject=actor_subject_from_request(request),
        )
        session.refresh(kluster)
        return kluster


@router.get("", response_model=list[KlusterRead])
def list_klusters(mission_id: str, request: Request):
    with get_session() as session:
        mission = session.get(Mission, mission_id)
        if not mission:
            raise HTTPException(status_code=404, detail="Mission not found")
        assert_mission_reader_or_admin(session=session, request=request, mission_id=mission_id)
        query = select(Kluster).where(Kluster.mission_id == mission_id)
        klusters = session.exec(query.order_by(Kluster.updated_at.desc())).all()
        return klusters


@router.get("/{kluster_id}", response_model=KlusterRead)
def get_kluster(mission_id: str, kluster_id: str, request: Request):
    with get_session() as session:
        mission = session.get(Mission, mission_id)
        if not mission:
            raise HTTPException(status_code=404, detail="Mission not found")
        assert_mission_reader_or_admin(session=session, request=request, mission_id=mission_id)
        kluster = session.get(Kluster, kluster_id)
        if not kluster or kluster.mission_id != mission_id:
            raise HTTPException(status_code=404, detail="Kluster not found")
        return kluster


@router.patch("/{kluster_id}", response_model=KlusterRead)
def update_kluster(mission_id: str, kluster_id: str, payload: KlusterCreate, request: Request):
    with get_session() as session:
        require_policy_action(
            session=session,
            action="kluster.update",
            request=request,
            approval_context=extract_approval_context(request),
            channel="api",
        )
        mission = session.get(Mission, mission_id)
        if not mission:
            raise HTTPException(status_code=404, detail="Mission not found")
        kluster = session.get(Kluster, kluster_id)
        if not kluster or kluster.mission_id != mission_id:
            raise HTTPException(status_code=404, detail="Kluster not found")
        assert_mission_writer_or_admin(session=session, request=request, mission_id=mission_id)
        before = kluster.dict()
        updates = enforce_schema_pack(
            request=request,
            entity_type="kluster",
            payload=payload.dict(),
            operation="update",
        )
        if not _owner_list(updates.get("owners")):
            raise HTTPException(status_code=422, detail="owners must include at least one owner")
        if payload.mission_id is not None and payload.mission_id != mission_id:
            raise HTTPException(status_code=409, detail="Payload mission_id must match route mission_id")
        updates["mission_id"] = mission_id
        for k, v in updates.items():
            setattr(kluster, k, v)
        kluster.updated_at = datetime.utcnow()
        session.add(kluster)
        session.commit()
        session.refresh(kluster)
        enqueue_ledger_event(
            session=session,
            mission_id=kluster.mission_id,
            kluster_id=kluster.id,
            entity_type="kluster",
            entity_id=kluster.id,
            action="update",
            before=before,
            after=kluster.dict(),
            actor_subject=actor_subject_from_request(request),
            source=request_source(request),
        )
        session.refresh(kluster)
        return kluster


@router.delete("/{kluster_id}")
def delete_kluster(mission_id: str, kluster_id: str, request: Request):
    with get_session() as session:
        require_policy_action(
            session=session,
            action="kluster.delete",
            request=request,
            approval_context=extract_approval_context(request),
            channel="api",
        )
        mission = session.get(Mission, mission_id)
        if not mission:
            raise HTTPException(status_code=404, detail="Mission not found")
        kluster = session.get(Kluster, kluster_id)
        if not kluster or kluster.mission_id != mission_id:
            raise HTTPException(status_code=404, detail="Kluster not found")
        assert_mission_owner_or_admin(session=session, request=request, mission_id=mission_id)

        blockers = {
            "tasks": len(session.exec(select(Task.id).where(Task.kluster_id == kluster_id)).all()),
            "docs": len(session.exec(select(Doc.id).where(Doc.kluster_id == kluster_id)).all()),
            "artifacts": len(session.exec(select(Artifact.id).where(Artifact.kluster_id == kluster_id)).all()),
            "epics": len(session.exec(select(Epic.id).where(Epic.kluster_id == kluster_id)).all()),
            "ingestion_jobs": len(
                session.exec(select(IngestionJob.id).where(IngestionJob.kluster_id == kluster_id)).all()
            ),
        }
        blocking = {k: v for k, v in blockers.items() if v}
        if blocking:
            raise HTTPException(
                status_code=409,
                detail=f"Kluster has linked entities: {blocking}",
            )

        before = kluster.dict()
        session.delete(kluster)
        session.commit()
        enqueue_ledger_event(
            session=session,
            mission_id=mission_id,
            kluster_id=kluster_id,
            entity_type="kluster",
            entity_id=kluster_id,
            action="delete",
            before=before,
            after=None,
            actor_subject=actor_subject_from_request(request),
            source=request_source(request),
        )
        return {"ok": True, "deleted_id": kluster_id}


# Compatibility helpers for in-process callers/tests that still use the old flat signatures.
def create_cluster(payload: KlusterCreate, request: Request):
    if not payload.mission_id:
        raise HTTPException(status_code=422, detail="mission_id is required")
    return create_kluster(payload.mission_id, payload, request)


def update_cluster(kluster_id: str, payload: KlusterCreate, request: Request):
    if not payload.mission_id:
        raise HTTPException(status_code=422, detail="mission_id is required")
    return update_kluster(payload.mission_id, kluster_id, payload, request)


def delete_cluster(kluster_id: str, request: Request):
    with get_session() as session:
        kluster = session.get(Kluster, kluster_id)
        if not kluster or not kluster.mission_id:
            raise HTTPException(status_code=404, detail="Kluster not found")
        mission_id = kluster.mission_id
    return delete_kluster(mission_id, kluster_id, request)
