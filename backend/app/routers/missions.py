from datetime import datetime
from fastapi import APIRouter, HTTPException, Request
from sqlmodel import select

from app.db import get_session
from app.models import Kluster, Mission, MissionRoleMembership
from app.schemas import (
    MissionCreate,
    MissionRead,
    MissionRoleRead,
    MissionRoleUpsert,
    MissionOwnerTransfer,
    MissionUpdate,
)
from app.services.authz import (
    actor_subject_from_request as authz_actor_subject,
    assert_mission_reader_or_admin,
    assert_mission_owner_or_admin,
    assert_mission_writer_or_admin,
    assert_platform_admin,
    readable_mission_ids_for_request,
    set_mission_owner,
    upsert_mission_role,
    remove_mission_role,
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
from app.services.keystone import ensure_mission_northstar

router = APIRouter(prefix="/missions", tags=["missions"])


def _owner_list(value: str | None) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


@router.post("", response_model=MissionRead)
def create_mission(payload: MissionCreate, request: Request):
    with get_session() as session:
        require_policy_action(
            session=session,
            action="mission.create",
            request=request,
            approval_context=extract_approval_context(request),
            channel="api",
        )
        existing = session.exec(select(Mission).where(Mission.name == payload.name)).first()
        if existing:
            raise HTTPException(status_code=409, detail="Mission name already exists")
        mission_data = payload.dict()
        mission_data = enforce_schema_pack(
            request=request,
            entity_type="mission",
            payload=mission_data,
            operation="create",
        )
        actor = authz_actor_subject(request)
        if not mission_data.get("owners") and actor not in {"unknown", "service-token"}:
            mission_data["owners"] = actor
        if not _owner_list(mission_data.get("owners")):
            raise HTTPException(status_code=422, detail="owners must include at least one owner")
        mission = Mission(id=new_hash_id(), **mission_data)
        while session.get(Mission, mission.id):
            mission.id = new_hash_id()
        ensure_mission_northstar(mission, actor=actor)
        session.add(mission)
        session.commit()
        session.refresh(mission)
        event = enqueue_ledger_event(
            session=session,
            mission_id=mission.id,
            kluster_id=None,
            entity_type="mission",
            entity_id=mission.id,
            action="create",
            before=None,
            after=mission.dict(),
            actor_subject=actor_subject_from_request(request),
            source=request_source(request),
            pending_uncommitted_changes=True,
        )
        try_publish_bootstrap_event(
            session=session,
            event_id=event.id,
            actor_subject=actor_subject_from_request(request),
        )
        session.refresh(mission)
        return mission


@router.get("", response_model=list[MissionRead])
def list_missions(request: Request):
    with get_session() as session:
        readable_ids = readable_mission_ids_for_request(session=session, request=request)
        missions = session.exec(select(Mission).order_by(Mission.updated_at.desc())).all()
        if not readable_ids and not missions:
            return []
        if not readable_ids:
            return []
        missions = [m for m in missions if m.id in readable_ids]
        return missions


@router.get("/{mission_id}", response_model=MissionRead)
def get_mission(mission_id: str, request: Request):
    with get_session() as session:
        assert_mission_reader_or_admin(session=session, request=request, mission_id=mission_id)
        mission = session.get(Mission, mission_id)
        if not mission:
            raise HTTPException(status_code=404, detail="Mission not found")
        return mission


@router.patch("/{mission_id}", response_model=MissionRead)
def update_mission(mission_id: str, payload: MissionUpdate, request: Request):
    with get_session() as session:
        require_policy_action(
            session=session,
            action="mission.update",
            request=request,
            approval_context=extract_approval_context(request),
            channel="api",
        )
        assert_mission_writer_or_admin(session=session, request=request, mission_id=mission_id)
        mission = session.get(Mission, mission_id)
        if not mission:
            raise HTTPException(status_code=404, detail="Mission not found")
        before = mission.dict()
        updates = payload.dict(exclude_unset=True)
        updates = enforce_schema_pack(
            request=request,
            entity_type="mission",
            payload=updates,
            operation="update",
        )
        if "owners" in updates and not _owner_list(updates.get("owners")):
            raise HTTPException(status_code=422, detail="owners must include at least one owner")
        for key, value in updates.items():
            setattr(mission, key, value)
        mission.updated_at = datetime.utcnow()
        session.add(mission)
        session.commit()
        session.refresh(mission)
        enqueue_ledger_event(
            session=session,
            mission_id=mission.id,
            kluster_id=None,
            entity_type="mission",
            entity_id=mission.id,
            action="update",
            before=before,
            after=mission.dict(),
            actor_subject=actor_subject_from_request(request),
            source=request_source(request),
        )
        session.refresh(mission)
        return mission


@router.delete("/{mission_id}")
def delete_mission(mission_id: str, request: Request):
    with get_session() as session:
        require_policy_action(
            session=session,
            action="mission.delete",
            request=request,
            approval_context=extract_approval_context(request),
            channel="api",
        )
        assert_mission_owner_or_admin(session=session, request=request, mission_id=mission_id)
        mission = session.get(Mission, mission_id)
        if not mission:
            raise HTTPException(status_code=404, detail="Mission not found")
        before = mission.dict()
        linked_cluster = session.exec(select(Kluster).where(Kluster.mission_id == mission_id)).first()
        if linked_cluster:
            raise HTTPException(
                status_code=409,
                detail="Mission has linked klusters; move or delete klusters first",
            )
        role_rows = session.exec(
            select(MissionRoleMembership).where(MissionRoleMembership.mission_id == mission_id)
        ).all()
        for row in role_rows:
            session.delete(row)
        session.delete(mission)
        session.commit()
        enqueue_ledger_event(
            session=session,
            mission_id=mission_id,
            kluster_id=None,
            entity_type="mission",
            entity_id=mission_id,
            action="delete",
            before=before,
            after=None,
            actor_subject=actor_subject_from_request(request),
            source=request_source(request),
        )
        return {"ok": True, "deleted_id": mission_id}


@router.post("/{mission_id}/owner", response_model=MissionRead)
def transfer_owner(mission_id: str, payload: MissionOwnerTransfer, request: Request):
    with get_session() as session:
        assert_platform_admin(request)
        mission = session.get(Mission, mission_id)
        if not mission:
            raise HTTPException(status_code=404, detail="Mission not found")
        before = mission.dict()
        set_mission_owner(session=session, mission=mission, new_owner=payload.new_owner)
        session.commit()
        session.refresh(mission)
        enqueue_ledger_event(
            session=session,
            mission_id=mission_id,
            kluster_id=None,
            entity_type="mission",
            entity_id=mission_id,
            action="owner_transfer",
            before=before,
            after=mission.dict(),
            actor_subject=actor_subject_from_request(request),
            source=request_source(request),
        )
        return mission


@router.get("/{mission_id}/roles", response_model=list[MissionRoleRead])
def list_mission_roles(mission_id: str, request: Request):
    with get_session() as session:
        assert_mission_owner_or_admin(session=session, request=request, mission_id=mission_id)
        roles = session.exec(
            select(MissionRoleMembership)
            .where(MissionRoleMembership.mission_id == mission_id)
            .order_by(MissionRoleMembership.created_at.asc())
        ).all()
        return roles


@router.post("/{mission_id}/roles", response_model=MissionRoleRead)
def add_or_update_mission_role(mission_id: str, payload: MissionRoleUpsert, request: Request):
    with get_session() as session:
        assert_mission_owner_or_admin(session=session, request=request, mission_id=mission_id)
        mission = session.get(Mission, mission_id)
        if not mission:
            raise HTTPException(status_code=404, detail="Mission not found")
        role = upsert_mission_role(
            session=session,
            mission_id=mission_id,
            subject=payload.subject,
            role=payload.role,
        )
        session.commit()
        session.refresh(role)
        return role


@router.delete("/{mission_id}/roles/{subject}")
def delete_mission_role(mission_id: str, subject: str, request: Request):
    with get_session() as session:
        assert_mission_owner_or_admin(session=session, request=request, mission_id=mission_id)
        removed = remove_mission_role(session=session, mission_id=mission_id, subject=subject)
        if not removed:
            raise HTTPException(status_code=404, detail="Role assignment not found")
        session.commit()
        return {"ok": True}
