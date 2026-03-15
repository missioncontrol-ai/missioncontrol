"""Centralized event persistence for the AI Console.

Extracted from ai.py so both the router and the ConsoleGateway
can write AiEvent rows through the same helper without circular imports.
"""
from __future__ import annotations

import json
from datetime import datetime

from app.models import AiEvent
from app.ai_console.contracts import NormalizedEvent


def emit_event(
    *,
    db,
    session_id: str,
    turn_id: int | None,
    event_type: str,
    payload: dict,
) -> AiEvent:
    """Persist a raw event dict as an AiEvent row."""
    event = AiEvent(
        session_id=session_id,
        turn_id=turn_id,
        event_type=event_type,
        payload_json=json.dumps(payload, separators=(",", ":"), default=str),
        created_at=datetime.utcnow(),
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


def emit_normalized(*, db, event: NormalizedEvent) -> AiEvent:
    """Persist a NormalizedEvent as an AiEvent row.

    The NormalizedEvent family/runtime_kind are merged into the payload
    so the existing AiEvent schema does not need to change.
    """
    payload = {
        "_family": event.family,
        "_runtime_kind": event.runtime_kind,
        "_schema_version": event.schema_version,
        **event.payload,
    }
    return emit_event(
        db=db,
        session_id=event.session_id,
        turn_id=event.turn_id,
        event_type=event.event_type,
        payload=payload,
    )
