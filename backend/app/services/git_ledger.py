import json
import uuid
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from sqlmodel import Session, select

from app.models import LedgerEvent, Mission, MissionRoleMembership
from app.services.persistence_publish import PublishRoutingError, publish_events_with_routing

from fastapi import Request


def enqueue_ledger_event(
    *,
    session: Session,
    mission_id: Optional[str],
    kluster_id: Optional[str],
    entity_type: str,
    entity_id: int | str,
    action: str,
    before: Optional[dict[str, Any]],
    after: Optional[dict[str, Any]],
    actor_subject: str,
    source: str,
    request_id: str = "",
    created_by_agent_id: Optional[int] = None,
    pending_uncommitted_changes: bool = False,
) -> LedgerEvent:
    now = datetime.utcnow()
    payload = {
        "schema_version": "v1",
        "event_id": str(uuid.uuid4()),
        "mission_id": mission_id,
        "kluster_id": kluster_id,
        "entity_type": entity_type,
        "entity_id": str(entity_id),
        "action": action,
        "source": source,
        "actor": actor_subject,
        "request_id": request_id,
        "before": before,
        "after": after,
        "recorded_at": now.isoformat(),
        "pending_uncommitted_changes": pending_uncommitted_changes,
    }
    event = LedgerEvent(
        event_id=payload["event_id"],
        mission_id=mission_id,
        kluster_id=kluster_id,
        entity_type=entity_type,
        entity_id=str(entity_id),
        action=action,
        payload_json=json.dumps(payload, separators=(",", ":"), default=_json_default),
        state="pending",
        created_by_agent_id=created_by_agent_id,
        created_by_subject=actor_subject,
        created_at=now,
        updated_at=now,
    )
    session.add(event)
    session.commit()
    session.refresh(event)
    return event


def actor_subject_from_request(request: Optional[Request]) -> str:
    if request is None:
        return "unknown"
    principal = getattr(request.state, "principal", None)
    if isinstance(principal, dict):
        return principal.get("email") or principal.get("subject") or "unknown"
    return "unknown"


def request_source(request: Optional[Request], default: str = "api") -> str:
    if request is None:
        return default
    path = getattr(request.url, "path", "")
    if path.startswith("/mcp"):
        return "mcp"
    return default


def list_pending_ledger_events(
    *,
    session: Session,
    mission_id: Optional[str] = None,
    created_by_subject: Optional[str] = None,
    limit: int = 100,
) -> list[LedgerEvent]:
    stmt = select(LedgerEvent).where(LedgerEvent.state == "pending")
    if mission_id is not None:
        stmt = stmt.where(LedgerEvent.mission_id == mission_id)
    if created_by_subject:
        stmt = stmt.where(LedgerEvent.created_by_subject == created_by_subject)
    stmt = stmt.order_by(LedgerEvent.created_at.asc()).limit(max(1, min(limit, 500)))
    return session.exec(stmt).all()


def get_entity_history(
    *,
    session: Session,
    mission_id: str,
    entity_type: str,
    entity_id: str,
    limit: int = 200,
) -> list[LedgerEvent]:
    stmt = (
        select(LedgerEvent)
        .where(LedgerEvent.mission_id == mission_id)
        .where(LedgerEvent.entity_type == entity_type)
        .where(LedgerEvent.entity_id == entity_id)
        .order_by(LedgerEvent.created_at.desc())
        .limit(max(1, min(limit, 1000)))
    )
    return session.exec(stmt).all()


def publish_pending_ledger_events(
    *,
    session: Session,
    mission_id: str,
    actor_subject: str,
) -> dict[str, Any]:
    pending = list_pending_ledger_events(session=session, mission_id=mission_id, limit=500)
    if not pending:
        return {"published_count": 0, "commit_sha": "", "branch": "", "repo_url": ""}
    return publish_events_with_routing(
        session=session,
        owner_subject=actor_subject,
        mission_id=mission_id,
        events=pending,
    )


def try_publish_bootstrap_event(
    *,
    session: Session,
    event_id: int,
    actor_subject: str,
) -> dict[str, Any]:
    event = session.get(LedgerEvent, event_id)
    if not event:
        return {"published": False, "reason": "event_not_found"}
    if event.state != "pending":
        return {"published": False, "reason": f"event_state_{event.state}"}

    try:
        publish_events_with_routing(
            session=session,
            owner_subject=actor_subject,
            mission_id=event.mission_id or "",
            events=[event],
        )
        return {"published": True}
    except PublishRoutingError as exc:
        return {"published": False, "reason": str(exc)}


def subject_can_publish_mission(
    *,
    session: Session,
    mission_id: str,
    subject: str,
) -> bool:
    mission = session.get(Mission, mission_id)
    if not mission:
        return False
    allowed = set(_split_csv(mission.owners)) | set(_split_csv(mission.contributors))
    rows = session.exec(
        select(MissionRoleMembership)
        .where(MissionRoleMembership.mission_id == mission_id)
        .where(MissionRoleMembership.role.in_(["mission_owner", "mission_contributor"]))
    ).all()
    allowed |= {r.subject for r in rows if r.subject}
    return _normalize_identity(subject) in {_normalize_identity(x) for x in allowed}


def _events_base_path() -> Path:
    return Path("events")


def _event_rel_path(events_base: Path, mission_key: str, event: LedgerEvent) -> Path:
    dt = event.created_at or datetime.utcnow()
    stamp = dt.strftime("%Y%m%dT%H%M%SZ")
    return (
        events_base
        / dt.strftime("%Y")
        / dt.strftime("%m")
        / dt.strftime("%d")
        / mission_key
        / event.entity_type
        / str(event.entity_id)
        / f"{stamp}-{event.event_id}.json"
    )


def _mission_key_map(*, session: Session, events: list[LedgerEvent]) -> dict[Optional[str], str]:
    ids = sorted({e.mission_id for e in events if e.mission_id is not None})
    out: dict[Optional[str], str] = {}
    if not ids:
        return out
    missions = session.exec(select(Mission).where(Mission.id.in_(ids))).all()
    for m in missions:
        out[m.id] = _mission_key(m.name, m.id)
    return out


def _mission_key(name: str, mission_id: str) -> str:
    slug = _slugify(name)
    return f"{slug}-{mission_id}"


def _mission_fallback_key(mission_id: Optional[str]) -> str:
    if mission_id is None:
        return "unassigned-0000"
    return f"mission-{mission_id}"


def _slugify(value: str) -> str:
    slug = value.strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    return slug or "mission"


def _maybe_write_bootstrap_pending(
    repo_dir: Path,
    missions_base: Path,
    mission_key: str,
    event: LedgerEvent,
    payload: dict[str, Any],
) -> str:
    if event.action != "create" or event.entity_type not in {"mission", "kluster", "task", "doc", "artifact"}:
        return ""
    pending = {
        "status": "Pending uncommitted changes",
        "entity_type": event.entity_type,
        "entity_id": event.entity_id,
        "event_id": event.event_id,
        "initiated_by": payload.get("actor", ""),
        "initiated_at": payload.get("recorded_at", ""),
        "description": _bootstrap_description(payload),
        "metadata": {
            "mission_id": event.mission_id,
            "kluster_id": event.kluster_id,
            "source": payload.get("source", ""),
        },
    }
    if event.entity_type == "mission":
        rel = missions_base / mission_key / f"{mission_key}-pending.json"
    elif event.entity_type == "kluster":
        kluster_id = event.kluster_id or str(event.entity_id)
        rel = missions_base / mission_key / "k" / kluster_id / f"{kluster_id}-pending.json"
    elif event.kluster_id:
        rel = missions_base / mission_key / "k" / event.kluster_id / f"{event.entity_type}-{event.entity_id}-pending.json"
    else:
        rel = missions_base / mission_key / f"{event.entity_type}-{event.entity_id}-pending.json"
    target = repo_dir / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(pending, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return str(rel)


def _bootstrap_description(payload: dict[str, Any]) -> str:
    after = payload.get("after") or {}
    if isinstance(after, dict):
        return str(after.get("description") or after.get("title") or after.get("name") or "")
    return ""


def _split_csv(value: str) -> list[str]:
    if not value:
        return []
    return [x.strip() for x in value.split(",") if x.strip()]


def _normalize_identity(value: str) -> str:
    return value.strip().lower()


def _json_default(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return obj.isoformat()
    return str(obj)
