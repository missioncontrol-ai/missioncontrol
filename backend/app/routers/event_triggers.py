"""Event triggers router — CRUD for EventTrigger records."""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlmodel import select

from app.db import get_session
from app.models import EventTrigger
from app.services.authz import actor_subject_from_request

logger = logging.getLogger(__name__)

router = APIRouter(tags=["event-triggers"])


class EventTriggerCreate(BaseModel):
    event_type: str
    predicate_json: Optional[str] = None
    target_type: str = "mesh_task"
    target_spec_json: str
    cooldown_seconds: int = 0


class EventTriggerOut(BaseModel):
    id: str
    owner_subject: str
    event_type: str
    predicate_json: Optional[str]
    target_type: str
    target_spec_json: str
    active: bool
    cooldown_seconds: int
    last_fired_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime


def _to_out(t: EventTrigger) -> EventTriggerOut:
    return EventTriggerOut(
        id=t.id,
        owner_subject=t.owner_subject,
        event_type=t.event_type,
        predicate_json=t.predicate_json,
        target_type=t.target_type,
        target_spec_json=t.target_spec_json,
        active=t.active,
        cooldown_seconds=t.cooldown_seconds,
        last_fired_at=t.last_fired_at,
        created_at=t.created_at,
        updated_at=t.updated_at,
    )


@router.post("", response_model=EventTriggerOut, status_code=201)
def create_trigger(body: EventTriggerCreate, request: Request):
    subject = actor_subject_from_request(request)
    now = datetime.utcnow()
    trigger = EventTrigger(
        id=str(uuid.uuid4()),
        owner_subject=subject,
        event_type=body.event_type,
        predicate_json=body.predicate_json,
        target_type=body.target_type,
        target_spec_json=body.target_spec_json,
        cooldown_seconds=body.cooldown_seconds,
        active=True,
        created_at=now,
        updated_at=now,
    )
    with get_session() as db:
        db.add(trigger)
        db.commit()
        db.refresh(trigger)
        return _to_out(trigger)


@router.get("", response_model=list[EventTriggerOut])
def list_triggers(request: Request):
    subject = actor_subject_from_request(request)
    with get_session() as db:
        triggers = db.exec(
            select(EventTrigger).where(
                EventTrigger.owner_subject == subject,
                EventTrigger.active == True,  # noqa: E712
            )
        ).all()
        return [_to_out(t) for t in triggers]


@router.get("/{trigger_id}", response_model=EventTriggerOut)
def get_trigger(trigger_id: str, request: Request):
    subject = actor_subject_from_request(request)
    with get_session() as db:
        trigger = db.exec(
            select(EventTrigger).where(EventTrigger.id == trigger_id)
        ).first()
        if trigger is None or trigger.owner_subject != subject:
            raise HTTPException(status_code=404, detail="Not found")
        return _to_out(trigger)


@router.delete("/{trigger_id}", status_code=204)
def deactivate_trigger(trigger_id: str, request: Request):
    subject = actor_subject_from_request(request)
    with get_session() as db:
        trigger = db.exec(
            select(EventTrigger).where(EventTrigger.id == trigger_id)
        ).first()
        if trigger is None or trigger.owner_subject != subject:
            raise HTTPException(status_code=404, detail="Not found")
        trigger.active = False
        trigger.updated_at = datetime.utcnow()
        db.add(trigger)
        db.commit()
