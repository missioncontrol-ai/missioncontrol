"""AgentScheduler — APScheduler-backed cron job runner for ScheduledAgentJob records.

Loads all enabled jobs from DB on startup, schedules them via AsyncIOScheduler,
and exposes add/remove helpers for dynamic management by the scheduled_jobs router.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Any, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None
_evaluator_task: Optional[asyncio.Task] = None


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler()
    return _scheduler


def _make_apscheduler_id(job_id: int) -> str:
    return f"scheduled-agent-job-{job_id}"


def _create_mesh_task_from_spec(db, spec: dict, owner_subject: str) -> None:
    """Create a MeshTask from a spec dict and publish the mesh event."""
    import uuid
    from app.models import MeshTask
    from app.services.mesh_events import publish_task_event

    kluster_id = spec.get("kluster_id", "")
    mission_id = spec.get("mission_id", "")
    task_id = str(uuid.uuid4())
    task = MeshTask(
        id=task_id,
        kluster_id=kluster_id,
        mission_id=mission_id,
        title=spec.get("title", "Scheduled task"),
        description=spec.get("description", ""),
        claim_policy=spec.get("claim_policy", "first_claim"),
        priority=spec.get("priority", 0),
        required_capabilities=json.dumps(spec.get("required_capabilities", [])),
        status="ready",
        created_by_subject=owner_subject,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(task)
    db.commit()
    publish_task_event(
        event_type="task_created",
        task_id=task_id,
        kluster_id=kluster_id,
        mission_id=mission_id,
        status="ready",
    )
    logger.info("mesh_task_created_from_spec task_id=%s kluster_id=%s", task_id, kluster_id)


async def _run_job(job_id: int) -> None:
    """Execute a scheduled agent job.

    If target_type == 'mesh_task' and target_spec_json is set, creates a MeshTask.
    Otherwise falls back to the original ai_session path.
    """
    from app.db import get_session
    from app.models import ScheduledAgentJob
    from sqlmodel import select

    with get_session() as db:
        job = db.exec(
            select(ScheduledAgentJob).where(ScheduledAgentJob.id == job_id)
        ).first()
        if job is None or not job.enabled:
            return

        target_type = getattr(job, "target_type", "ai_session") or "ai_session"

        if target_type == "mesh_task" and job.target_spec_json:
            try:
                spec = json.loads(job.target_spec_json)
                _create_mesh_task_from_spec(db, spec, job.owner_subject)
                job.last_run_at = datetime.utcnow()
                job.updated_at = datetime.utcnow()
                db.add(job)
                db.commit()
                logger.info("scheduled_job_mesh_task_complete job_id=%s", job_id)
            except Exception as exc:
                logger.error("scheduled_job_mesh_task_error job_id=%s error=%s", job_id, exc)
            return

        # ai_session path (original)
        from app.ai_console.gateway import get_gateway
        from app.models import AiSession, AiTurn
        from app.services.ids import new_hash_id

        policy_dict: dict[str, Any] = {}
        try:
            policy_dict = json.loads(job.policy_json or "{}")
        except Exception:
            pass

        gateway = get_gateway()
        session_id = new_hash_id()

        try:
            ai_session = await gateway.create_session(
                db=db,
                subject=job.owner_subject,
                session_id=session_id,
                title=f"Scheduled: {job.name}",
                runtime_kind=job.runtime_kind,
                policy_dict=policy_dict,
            )

            user_turn = AiTurn(
                session_id=ai_session.id,
                role="user",
                content_json=json.dumps({"text": job.initial_prompt}, separators=(",", ":")),
                created_at=datetime.utcnow(),
            )
            db.add(user_turn)
            db.commit()
            db.refresh(user_turn)

            await gateway.process_turn(
                db=db,
                session_row=ai_session,
                user_turn=user_turn,
                message=job.initial_prompt,
                subject=job.owner_subject,
            )

            job.last_run_at = datetime.utcnow()
            job.last_session_id = ai_session.id
            job.updated_at = datetime.utcnow()
            db.add(job)
            db.commit()
            logger.info("scheduled_job_complete job_id=%s session_id=%s", job_id, ai_session.id)

        except Exception as exc:
            logger.error("scheduled_job_error job_id=%s error=%s", job_id, exc)


def schedule_job(job_id: int, cron_expr: str) -> None:
    """Add or replace a job in the scheduler."""
    sched = get_scheduler()
    apscheduler_id = _make_apscheduler_id(job_id)
    try:
        sched.remove_job(apscheduler_id)
    except Exception:
        pass
    sched.add_job(
        _run_job,
        CronTrigger.from_crontab(cron_expr),
        id=apscheduler_id,
        args=[job_id],
        replace_existing=True,
    )
    logger.info("scheduled_job_registered job_id=%s cron=%s", job_id, cron_expr)


def unschedule_job(job_id: int) -> None:
    """Remove a job from the scheduler."""
    sched = get_scheduler()
    try:
        sched.remove_job(_make_apscheduler_id(job_id))
        logger.info("scheduled_job_removed job_id=%s", job_id)
    except Exception:
        pass


def start_scheduler() -> None:
    """Load all enabled jobs from DB and start the scheduler."""
    from app.db import get_session
    from app.models import ScheduledAgentJob
    from sqlmodel import select

    sched = get_scheduler()
    loaded = 0
    try:
        with get_session() as db:
            jobs = db.exec(
                select(ScheduledAgentJob).where(ScheduledAgentJob.enabled == True)  # noqa: E712
            ).all()
            for job in jobs:
                try:
                    schedule_job(job.id, job.cron_expr)
                    loaded += 1
                except Exception as exc:
                    logger.warning("Failed to schedule job %s: %s", job.id, exc)
    except Exception as exc:
        logger.warning("Failed to load scheduled jobs from DB: %s", exc)

    sched.start()
    logger.info("agent_scheduler_started loaded_jobs=%d", loaded)


def stop_scheduler() -> None:
    """Stop the scheduler gracefully."""
    try:
        get_scheduler().shutdown(wait=False)
        logger.info("agent_scheduler_stopped")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Event trigger evaluator
# ---------------------------------------------------------------------------


def _predicate_matches(predicate: dict, event: dict) -> bool:
    """Returns True if all predicate key/value pairs match the event."""
    return all(event.get(k) == v for k, v in predicate.items())


async def _evaluate_triggers(event: dict) -> None:
    """Check all active EventTriggers against this event."""
    from app.models import EventTrigger
    from app.db import get_session
    from sqlmodel import select

    with get_session() as session:
        triggers = list(session.exec(
            select(EventTrigger).where(
                EventTrigger.active == True,  # noqa: E712
                EventTrigger.event_type == event.get("event"),
            )
        ).all())

        now = datetime.utcnow()
        for trigger in triggers:
            # Check cooldown
            if trigger.last_fired_at and trigger.cooldown_seconds:
                elapsed = (now - trigger.last_fired_at).total_seconds()
                if elapsed < trigger.cooldown_seconds:
                    continue

            # Check predicate
            if trigger.predicate_json:
                try:
                    predicate = json.loads(trigger.predicate_json)
                    if not _predicate_matches(predicate, event):
                        continue
                except Exception:
                    continue

            # Fire the trigger
            try:
                spec = json.loads(trigger.target_spec_json)
                if trigger.target_type == "mesh_task":
                    _create_mesh_task_from_spec(session, spec, trigger.owner_subject)
                # ai_session trigger type: future work

                trigger.last_fired_at = now
                session.add(trigger)
                session.commit()
                logger.info("EventTrigger %s fired on event %s", trigger.id, event.get("event"))
            except Exception as e:
                logger.error("EventTrigger %s failed to fire: %s", trigger.id, e)


def start_event_trigger_listener() -> None:
    """Subscribe to the mesh event bus and evaluate triggers on each event."""
    from app.services.mesh_events import subscribe

    q = subscribe("__all_events__")

    async def loop():
        while True:
            try:
                event = await asyncio.wait_for(q.get(), timeout=5.0)
                await _evaluate_triggers(event)
            except asyncio.TimeoutError:
                pass
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Event trigger listener error: %s", e)

    global _evaluator_task
    _evaluator_task = asyncio.create_task(loop(), name="event-trigger-evaluator")
    logger.info("event_trigger_listener_started")


def stop_event_trigger_listener() -> None:
    """Cancel the event trigger listener task."""
    global _evaluator_task
    if _evaluator_task and not _evaluator_task.done():
        _evaluator_task.cancel()
    _evaluator_task = None
