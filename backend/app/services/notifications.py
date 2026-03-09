import json
import os
from datetime import datetime
from urllib import request as urllib_request

from sqlmodel import select

from app.db import get_session
from app.models import SlackChannelBinding
from app.services.chat_providers import get_chat_provider
from app.services.log_export import emit_structured_log


def emit_controlplane_event(*, req, event_type: str, payload: dict) -> None:
    event = {
        "event_type": event_type,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "payload": payload,
    }
    emit_structured_log(
        {
            "event_type": "controlplane.event",
            "channel": "controlplane",
            "controlplane_event_type": event_type,
            "mission_id": str(payload.get("mission_id") or ""),
            "payload": payload,
        }
    )
    _emit_mqtt(req=req, event=event)
    _emit_webhook(event=event)
    _emit_chat_channel_messages(event_type=event_type, payload=payload)


def _emit_mqtt(*, req, event: dict) -> None:
    try:
        app = getattr(req, "app", None) if req is not None else None
        mqtt_service = getattr(getattr(app, "state", None), "mqtt", None)
        if mqtt_service is not None:
            mqtt_service.publish("missioncontrol/events", event)
    except Exception:
        return


def _emit_webhook(*, event: dict) -> None:
    url = (os.getenv("MC_NOTIFICATION_WEBHOOK_URL") or "").strip()
    if not url:
        return
    try:
        body = json.dumps(event, separators=(",", ":")).encode("utf-8")
        req = urllib_request.Request(
            url=url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib_request.urlopen(req, timeout=2):
            pass
    except Exception:
        return


def _emit_chat_channel_messages(*, event_type: str, payload: dict) -> None:
    # Prevent feedback loops by suppressing fanout for raw inbound chat callbacks.
    if event_type.endswith(".event.received") or event_type.endswith(".command.received") or event_type.endswith(
        ".interaction.received"
    ):
        return
    mission_id = str(payload.get("mission_id") or "").strip()
    if not mission_id:
        return
    try:
        with get_session() as session:
            rows = session.exec(
                select(SlackChannelBinding).where(SlackChannelBinding.mission_id == mission_id)
            ).all()
            for row in rows:
                provider_name = (row.provider or "slack").strip().lower()
                provider = get_chat_provider(provider_name)
                if provider is None:
                    continue
                metadata = {}
                if row.channel_metadata_json:
                    try:
                        parsed = json.loads(row.channel_metadata_json)
                        if isinstance(parsed, dict):
                            metadata = parsed
                    except Exception:
                        metadata = {}
                provider.send_event_notification(
                    channel_id=row.channel_id,
                    workspace_external_id=row.workspace_external_id or "",
                    channel_metadata=metadata,
                    event_type=event_type,
                    payload=payload,
                )
    except Exception:
        return
