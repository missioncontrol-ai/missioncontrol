"""
MeshDispatcher — belt-and-braces background job that promotes tasks from
blocked/pending to ready when all their dependencies are finished.

This catches any tasks that missed a _unblock_dependents call (e.g. due to
a process restart mid-transaction) and is also the mechanism that makes the
mesh restart-safe.

Cadence: MC_MESH_DISPATCH_INTERVAL_SECONDS (default 5).
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Optional

from sqlmodel import Session, select

from app.db import engine
from app.models import MeshTask

logger = logging.getLogger(__name__)

DISPATCH_INTERVAL_SECONDS = int(os.getenv("MC_MESH_DISPATCH_INTERVAL_SECONDS", "5"))

_dispatcher_job_id: Optional[str] = None


def tick() -> int:
    """
    Single dispatcher tick: promote any blocked/pending task whose deps are all
    finished to ready, and emit task_ready for each.

    Returns number of tasks promoted.
    """
    with Session(engine) as session:
        candidates = session.exec(
            select(MeshTask).where(MeshTask.status.in_(["pending", "blocked"]))
        ).all()

        promoted: list[MeshTask] = []
        now = datetime.utcnow()

        for task in candidates:
            dep_ids: list = json.loads(task.depends_on or "[]")
            if not dep_ids:
                # No deps — should already be ready; promote defensively
                task.status = "ready"
                task.updated_at = now
                session.add(task)
                promoted.append(task)
                continue

            dep_tasks = session.exec(
                select(MeshTask).where(MeshTask.id.in_(dep_ids))
            ).all()

            if all(d.status == "finished" for d in dep_tasks):
                task.status = "ready"
                task.updated_at = now
                session.add(task)
                promoted.append(task)

        if promoted:
            session.commit()
            from app.services.mesh_events import publish_task_event
            for task in promoted:
                publish_task_event(
                    "task_ready",
                    task.id,
                    task.kluster_id,
                    task.mission_id or "",
                    status="ready",
                )
            logger.info("MeshDispatcher promoted %d task(s) to ready", len(promoted))

    return len(promoted)


async def _tick_async() -> None:
    try:
        count = tick()
        if count:
            logger.debug("MeshDispatcher: promoted %d task(s)", count)
    except Exception as exc:
        logger.error("MeshDispatcher tick failed: %s", exc)


def start_dispatcher() -> None:
    global _dispatcher_job_id
    from app.services.agent_scheduler import get_scheduler

    scheduler = get_scheduler()
    job = scheduler.add_job(
        _tick_async,
        "interval",
        seconds=DISPATCH_INTERVAL_SECONDS,
        id="mesh-dispatcher",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    _dispatcher_job_id = job.id
    logger.info("MeshDispatcher registered (every %ds)", DISPATCH_INTERVAL_SECONDS)


def stop_dispatcher() -> None:
    global _dispatcher_job_id
    from app.services.agent_scheduler import get_scheduler

    scheduler = get_scheduler()
    try:
        scheduler.remove_job("mesh-dispatcher")
        logger.info("MeshDispatcher stopped")
    except Exception:
        pass
    _dispatcher_job_id = None
