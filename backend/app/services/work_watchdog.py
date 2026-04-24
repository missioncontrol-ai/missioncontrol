"""
Centralized work watchdog — runs every 15s to expire stale task leases
(return to 'ready') and emit pg_notify for freed tasks on Postgres.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import text
from sqlmodel import Session, select

from app.db import engine
from app.models import MeshTask

logger = logging.getLogger(__name__)

WATCHDOG_INTERVAL_SECONDS = 15

_watchdog_job_id: Optional[str] = None


def _tick(session: Session) -> int:
    """
    Single watchdog tick: expire stale leases.
    Returns number of tasks freed.
    """
    now = datetime.utcnow()

    stale = session.exec(
        select(MeshTask)
        .where(MeshTask.status.in_(["claimed", "running"]))
        .where(MeshTask.claim_policy != "broadcast")
        .where(MeshTask.lease_expires_at != None)  # noqa: E711
        .where(MeshTask.lease_expires_at < now)
    ).all()

    freed = 0
    for task in stale:
        task.status = "ready"
        task.claimed_by_agent_id = None
        task.claim_lease_id = None
        task.lease_expires_at = None
        task.updated_at = now
        session.add(task)

        # Emit NOTIFY on Postgres so listeners can react immediately
        bind = session.get_bind()
        dialect_name = getattr(bind, "dialect", None)
        dialect_name = dialect_name.name if dialect_name else engine.dialect.name
        if dialect_name == "postgresql":
            payload = json.dumps({
                "type": "lease_expired",
                "task_id": task.id,
                "kluster_id": task.kluster_id,
                "mission_id": task.mission_id,
            })
            session.execute(
                text("SELECT pg_notify('mesh_events', :payload)"),
                {"payload": payload},
            )

        logger.info(
            "Watchdog freed stale task %s (was claimed by %s)",
            task.id,
            task.claimed_by_agent_id,
        )
        freed += 1

    session.commit()

    # Fan out events after commit (best-effort)
    from app.services.mesh_events import publish_task_event
    for task in stale:
        publish_task_event("lease_expired", task.id, task.kluster_id, task.mission_id or "", status="ready")
        publish_task_event("task_ready", task.id, task.kluster_id, task.mission_id or "", status="ready")

    return freed


async def _watchdog_tick_async() -> None:
    """APScheduler async job entry point."""
    try:
        with Session(engine) as session:
            freed = _tick(session)
            if freed:
                logger.debug("Watchdog freed %d stale task(s)", freed)
    except Exception as exc:
        logger.error("Watchdog tick failed: %s", exc)


def start_watchdog() -> None:
    """Register the watchdog job with the APScheduler instance. Call from startup."""
    global _watchdog_job_id
    from app.services.agent_scheduler import get_scheduler

    scheduler = get_scheduler()
    job = scheduler.add_job(
        _watchdog_tick_async,
        "interval",
        seconds=WATCHDOG_INTERVAL_SECONDS,
        id="work-watchdog",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    _watchdog_job_id = job.id
    logger.info("Work watchdog registered (every %ds)", WATCHDOG_INTERVAL_SECONDS)


def stop_watchdog() -> None:
    """Remove the watchdog job. Call from shutdown."""
    global _watchdog_job_id
    from app.services.agent_scheduler import get_scheduler

    scheduler = get_scheduler()
    try:
        scheduler.remove_job("work-watchdog")
        logger.info("Work watchdog stopped")
    except Exception:
        pass
    _watchdog_job_id = None
