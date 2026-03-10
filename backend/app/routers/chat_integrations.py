import json
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from sqlmodel import select

from app.db import get_session
from app.models import SlackChannelBinding
from app.schemas import SlackChannelBindingCreate, SlackChannelBindingRead
from app.services.authz import (
    actor_subject_from_request,
    assert_mission_owner_or_admin,
    assert_mission_reader_or_admin,
)
from app.services.chat_providers import get_chat_provider
from app.services.pagination import bounded_limit, limit_query

router = APIRouter(prefix="/integrations/chat", tags=["chat"])


def _serialize_binding(row: SlackChannelBinding) -> dict:
    metadata = {}
    if row.channel_metadata_json:
        try:
            parsed = json.loads(row.channel_metadata_json)
            if isinstance(parsed, dict):
                metadata = parsed
        except Exception:
            metadata = {}
    return {
        "id": row.id,
        "provider": row.provider or "slack",
        "mission_id": row.mission_id,
        "workspace_external_id": row.workspace_external_id or "",
        "channel_id": row.channel_id,
        "channel_name": row.channel_name,
        "channel_metadata": metadata,
        "created_by": row.created_by,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _normalize_provider(provider: str | None) -> str:
    name = (provider or "slack").strip().lower() or "slack"
    if get_chat_provider(name) is None:
        raise HTTPException(status_code=400, detail=f"Unsupported chat provider: {name}")
    return name


@router.post("/bindings", response_model=SlackChannelBindingRead)
def create_chat_binding(payload: SlackChannelBindingCreate, request: Request):
    provider = _normalize_provider(payload.provider)
    with get_session() as session:
        assert_mission_owner_or_admin(session=session, request=request, mission_id=payload.mission_id)
        existing = session.exec(
            select(SlackChannelBinding)
            .where(SlackChannelBinding.provider == provider)
            .where(SlackChannelBinding.mission_id == payload.mission_id)
            .where(SlackChannelBinding.channel_id == payload.channel_id)
        ).first()
        if existing:
            return _serialize_binding(existing)
        row = SlackChannelBinding(
            provider=provider,
            mission_id=payload.mission_id,
            workspace_external_id=payload.workspace_external_id or "",
            channel_id=payload.channel_id,
            channel_name=payload.channel_name or "",
            channel_metadata_json=json.dumps(payload.channel_metadata or {}, separators=(",", ":")),
            created_by=actor_subject_from_request(request),
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return _serialize_binding(row)


@router.get("/bindings", response_model=list[SlackChannelBindingRead])
def list_chat_bindings(mission_id: str, request: Request, provider: str = "slack", limit: int = limit_query()):
    provider_name = _normalize_provider(provider)
    with get_session() as session:
        assert_mission_reader_or_admin(session=session, request=request, mission_id=mission_id)
        rows = session.exec(
            select(SlackChannelBinding)
            .where(SlackChannelBinding.provider == provider_name)
            .where(SlackChannelBinding.mission_id == mission_id)
            .order_by(SlackChannelBinding.updated_at.desc())
            .limit(bounded_limit(limit))
        ).all()
        return [_serialize_binding(row) for row in rows]


@router.delete("/bindings/{binding_id}")
def delete_chat_binding(binding_id: int, request: Request):
    with get_session() as session:
        row = session.get(SlackChannelBinding, binding_id)
        if not row:
            raise HTTPException(status_code=404, detail="Chat binding not found")
        assert_mission_owner_or_admin(session=session, request=request, mission_id=row.mission_id)
        session.delete(row)
        session.commit()
        return {"ok": True, "deleted_id": binding_id}
