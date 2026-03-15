from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from sqlmodel import select

from app.db import get_session
from app.models import (
    LedgerEvent,
    MissionPersistencePolicy,
    MissionPersistenceRoute,
    PublicationRecord,
    RepoBinding,
    RepoConnection,
)
from app.services.authz import assert_mission_owner_or_admin
from app.services.persistence_publish import (
    PublishRoutingError,
    get_publication_status,
    publish_events_with_routing,
    resolve_publish_plan,
)

router = APIRouter(prefix="/persistence", tags=["persistence"])


def _actor_subject(request: Request) -> str:
    principal = getattr(request.state, "principal", None)
    if not isinstance(principal, dict):
        raise HTTPException(status_code=401, detail="Unauthorized")
    return str(principal.get("email") or principal.get("subject") or "").strip() or "unknown"


@router.get("/connections")
def list_connections(request: Request):
    actor = _actor_subject(request)
    with get_session() as session:
        rows = session.exec(
            select(RepoConnection).where(RepoConnection.owner_subject == actor).order_by(RepoConnection.updated_at.desc())
        ).all()
        return rows


@router.post("/connections")
def create_connection(payload: dict, request: Request):
    actor = _actor_subject(request)
    now = datetime.utcnow()
    row = RepoConnection(
        owner_subject=actor,
        name=str(payload.get("name") or "").strip(),
        provider=str(payload.get("provider") or "github_app").strip(),
        host=str(payload.get("host") or "github.com").strip(),
        repo_path=str(payload.get("repo_path") or "").strip(),
        default_branch=str(payload.get("default_branch") or "main").strip(),
        credential_ref=str(payload.get("credential_ref") or "").strip(),
        options_json=str(payload.get("options_json") or "{}"),
        created_at=now,
        updated_at=now,
    )
    if not row.name or not row.repo_path:
        raise HTTPException(status_code=400, detail="name and repo_path are required")
    with get_session() as session:
        session.add(row)
        session.commit()
        session.refresh(row)
        return row


@router.get("/bindings")
def list_bindings(request: Request):
    actor = _actor_subject(request)
    with get_session() as session:
        rows = session.exec(
            select(RepoBinding).where(RepoBinding.owner_subject == actor).order_by(RepoBinding.updated_at.desc())
        ).all()
        return rows


@router.post("/bindings")
def create_binding(payload: dict, request: Request):
    actor = _actor_subject(request)
    now = datetime.utcnow()
    name = str(payload.get("name") or "").strip()
    connection_id = int(payload.get("connection_id") or 0)
    if not name or connection_id <= 0:
        raise HTTPException(status_code=400, detail="name and connection_id are required")
    with get_session() as session:
        conn = session.get(RepoConnection, connection_id)
        if not conn or conn.owner_subject != actor:
            raise HTTPException(status_code=404, detail="connection not found")
        row = RepoBinding(
            owner_subject=actor,
            name=name,
            connection_id=connection_id,
            branch_override=str(payload.get("branch_override") or "").strip(),
            base_path=str(payload.get("base_path") or "missions").strip().strip("/"),
            active=bool(payload.get("active", True)),
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return row


@router.get("/missions/{mission_id}/policy")
def get_policy(mission_id: str, request: Request):
    with get_session() as session:
        assert_mission_owner_or_admin(session=session, request=request, mission_id=mission_id)
        policy = session.exec(
            select(MissionPersistencePolicy).where(MissionPersistencePolicy.mission_id == mission_id)
        ).first()
        routes = session.exec(
            select(MissionPersistenceRoute)
            .where(MissionPersistenceRoute.mission_id == mission_id)
            .where(MissionPersistenceRoute.active == True)  # noqa: E712
            .order_by(MissionPersistenceRoute.id.asc())
        ).all()
        return {"policy": policy, "routes": routes}


@router.put("/missions/{mission_id}/policy")
def put_policy(mission_id: str, payload: dict, request: Request):
    actor = _actor_subject(request)
    with get_session() as session:
        assert_mission_owner_or_admin(session=session, request=request, mission_id=mission_id)
        now = datetime.utcnow()
        policy = session.exec(
            select(MissionPersistencePolicy).where(MissionPersistencePolicy.mission_id == mission_id)
        ).first()
        if not policy:
            policy = MissionPersistencePolicy(
                mission_id=mission_id,
                created_at=now,
                updated_at=now,
            )
        policy.default_binding_id = payload.get("default_binding_id")
        policy.fallback_mode = str(payload.get("fallback_mode") or "fail_closed")
        policy.require_approval = bool(payload.get("require_approval", False))
        policy.updated_at = now
        session.add(policy)

        session.exec(
            MissionPersistenceRoute.__table__.delete().where(MissionPersistenceRoute.mission_id == mission_id)
        )
        for item in payload.get("routes", []) or []:
            row = MissionPersistenceRoute(
                mission_id=mission_id,
                entity_kind=str(item.get("entity_kind") or "").strip(),
                event_kind=str(item.get("event_kind") or "").strip(),
                binding_id=int(item.get("binding_id")),
                branch_override=str(item.get("branch_override") or "").strip(),
                path_template=str(item.get("path_template") or "missions/{mission_id}/{entity_kind}/{entity_id}.json"),
                format=str(item.get("format") or "json_v1"),
                active=bool(item.get("active", True)),
                created_at=now,
                updated_at=now,
            )
            if row.entity_kind:
                session.add(row)
        session.commit()
        return {"ok": True, "mission_id": mission_id, "updated_by": actor}


@router.post("/publish/plan")
def publish_plan(payload: dict, request: Request):
    actor = _actor_subject(request)
    mission_id = str(payload.get("mission_id") or "").strip()
    entity_kind = str(payload.get("entity_kind") or "").strip()
    event_kind = str(payload.get("event_kind") or "").strip()
    entity_id = str(payload.get("entity_id") or "").strip()
    if not mission_id or not entity_kind or not event_kind or not entity_id:
        raise HTTPException(status_code=400, detail="mission_id, entity_kind, event_kind, entity_id are required")
    with get_session() as session:
        assert_mission_owner_or_admin(session=session, request=request, mission_id=mission_id)
        try:
            plan = resolve_publish_plan(
                session=session,
                owner_subject=actor,
                mission_id=mission_id,
                entity_kind=entity_kind,
                event_kind=event_kind,
                entity_id=entity_id,
            )
        except PublishRoutingError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {
            "mission_id": mission_id,
            "entity_kind": entity_kind,
            "event_kind": event_kind,
            "entity_id": entity_id,
            "binding_id": plan.binding.id,
            "binding_name": plan.binding.name,
            "provider": plan.connection.provider,
            "repo": f"{plan.connection.host}/{plan.connection.repo_path}",
            "branch": plan.branch,
            "path": plan.rel_path,
            "format": plan.format,
        }


@router.post("/publish/execute")
def publish_execute(payload: dict, request: Request):
    actor = _actor_subject(request)
    mission_id = str(payload.get("mission_id") or "").strip()
    limit = max(1, min(int(payload.get("limit") or 500), 500))
    if not mission_id:
        raise HTTPException(status_code=400, detail="mission_id is required")
    with get_session() as session:
        assert_mission_owner_or_admin(session=session, request=request, mission_id=mission_id)
        events = session.exec(
            select(LedgerEvent)
            .where(LedgerEvent.mission_id == mission_id)
            .where(LedgerEvent.state == "pending")
            .order_by(LedgerEvent.created_at.asc())
            .limit(limit)
        ).all()
        if not events:
            return {"published_count": 0, "commit_sha": "", "branch": "", "repo_url": ""}
        try:
            return publish_events_with_routing(
                session=session,
                owner_subject=actor,
                mission_id=mission_id,
                events=events,
            )
        except PublishRoutingError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/publish/records")
def list_publication_records(request: Request, mission_id: str | None = None, limit: int = 20):
    actor = _actor_subject(request)
    with get_session() as session:
        if mission_id:
            assert_mission_owner_or_admin(session=session, request=request, mission_id=mission_id)
        rows = get_publication_status(
            session=session,
            owner_subject=actor,
            mission_id=mission_id,
            limit=limit,
        )
        return {"records": rows}


@router.get("/publish/records/{record_id}")
def get_publication_record(record_id: int, request: Request):
    actor = _actor_subject(request)
    with get_session() as session:
        row = session.get(PublicationRecord, record_id)
        if not row or row.owner_subject != actor:
            raise HTTPException(status_code=404, detail="publication record not found")
        if row.mission_id:
            assert_mission_owner_or_admin(session=session, request=request, mission_id=row.mission_id)
        return {"record": row}
