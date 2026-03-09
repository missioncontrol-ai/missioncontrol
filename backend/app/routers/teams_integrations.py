import hashlib
import json

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from app.db import get_session
from app.models import ChatInboundReceipt, SlackChannelBinding
from app.services.chat_providers import get_teams_provider
from app.services.notifications import emit_controlplane_event

router = APIRouter(prefix="/integrations/teams", tags=["teams"])
provider = get_teams_provider()


def _binding_exists(*, mission_id: str, channel_id: str) -> bool:
    with get_session() as session:
        row = session.exec(
            select(SlackChannelBinding)
            .where(SlackChannelBinding.provider == "teams")
            .where(SlackChannelBinding.mission_id == mission_id)
            .where(SlackChannelBinding.channel_id == channel_id)
        ).first()
        return row is not None


def _event_key(*, event_type: str, mission_id: str, channel_id: str, raw: bytes) -> str:
    raw_hash = hashlib.sha256(raw).hexdigest()
    return f"teams:{event_type}:{mission_id}:{channel_id}:{raw_hash}"


def _record_receipt(*, event_key: str, event_type: str) -> bool:
    with get_session() as session:
        row = ChatInboundReceipt(provider="teams", event_key=event_key, event_type=event_type)
        session.add(row)
        try:
            session.commit()
            return True
        except IntegrityError:
            session.rollback()
            return False


@router.post("/events")
async def teams_events(request: Request):
    raw = await request.body()
    valid, reason = provider.verify(headers=request.headers, body=raw)
    if not valid:
        raise HTTPException(status_code=401, detail={"code": "teams_signature_invalid", "reason": reason})
    try:
        payload = json.loads(raw.decode("utf-8") or "{}")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid Teams payload")

    event_type = str(payload.get("type") or payload.get("eventType") or "")
    channel_id = str(payload.get("channel_id") or payload.get("conversationId") or "").strip()
    mission_id = str(payload.get("mission_id") or "").strip()

    if mission_id and channel_id and not _binding_exists(mission_id=mission_id, channel_id=channel_id):
        raise HTTPException(status_code=403, detail="Teams channel is not bound to mission")

    dedupe_key = _event_key(
        event_type=event_type or "event",
        mission_id=mission_id,
        channel_id=channel_id,
        raw=raw,
    )
    if not _record_receipt(event_key=dedupe_key, event_type="event"):
        return {"ok": True, "duplicate": True}

    emit_controlplane_event(
        req=request,
        event_type="teams.event.received",
        payload={
            "event_type": event_type,
            "mission_id": mission_id,
            "channel_id": channel_id,
        },
    )

    return {
        "ok": True,
        "provider": "teams",
        "text": "Event received.",
    }
