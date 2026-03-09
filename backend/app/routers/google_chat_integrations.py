import hashlib
import json

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from app.db import get_session
from app.models import ChatInboundReceipt, SlackChannelBinding
from app.services.chat_providers import get_google_chat_provider
from app.services.notifications import emit_controlplane_event

router = APIRouter(prefix="/integrations/google-chat", tags=["google-chat"])
provider = get_google_chat_provider()


def _binding_exists(*, mission_id: str, channel_id: str) -> bool:
    with get_session() as session:
        row = session.exec(
            select(SlackChannelBinding)
            .where(SlackChannelBinding.provider == "google_chat")
            .where(SlackChannelBinding.mission_id == mission_id)
            .where(SlackChannelBinding.channel_id == channel_id)
        ).first()
        return row is not None


def _event_key(*, event_type: str, mission_id: str, channel_id: str, raw: bytes) -> str:
    raw_hash = hashlib.sha256(raw).hexdigest()
    return f"google_chat:{event_type}:{mission_id}:{channel_id}:{raw_hash}"


def _record_receipt(*, event_key: str, event_type: str) -> bool:
    with get_session() as session:
        row = ChatInboundReceipt(provider="google_chat", event_key=event_key, event_type=event_type)
        session.add(row)
        try:
            session.commit()
            return True
        except IntegrityError:
            session.rollback()
            return False


@router.post("/events")
async def google_chat_events(request: Request):
    raw = await request.body()
    valid, reason = provider.verify(headers=request.headers, body=raw)
    if not valid:
        raise HTTPException(status_code=401, detail={"code": "google_chat_signature_invalid", "reason": reason})
    try:
        payload = json.loads(raw.decode("utf-8") or "{}")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid Google Chat payload")

    event_type = str(payload.get("type") or "")
    if event_type == "ADDED_TO_SPACE":
        return {"text": "MissionControl connected. Bind this space to a mission to enable actions."}

    space = payload.get("space") if isinstance(payload.get("space"), dict) else {}
    channel_id = str(space.get("name") or "").strip()
    mission_id = str(payload.get("mission_id") or "").strip()

    if mission_id and channel_id and not _binding_exists(mission_id=mission_id, channel_id=channel_id):
        raise HTTPException(status_code=403, detail="Google Chat space is not bound to mission")

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
        event_type="google_chat.event.received",
        payload={
            "event_type": event_type,
            "mission_id": mission_id,
            "channel_id": channel_id,
        },
    )

    return {
        "ok": True,
        "provider": "google_chat",
        "text": "Event received.",
    }
