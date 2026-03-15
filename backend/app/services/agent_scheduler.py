"""AgentScheduler — APScheduler-backed cron job runner for ScheduledAgentJob records.

Loads all enabled jobs from DB on startup, schedules them via AsyncIOScheduler,
and exposes add/remove helpers for dynamic management by the scheduled_jobs router.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler()
    return _scheduler


def _make_apscheduler_id(job_id: int) -> str:
    return f"scheduled-agent-job-{job_id}"


async def _run_job(job_id: int) -> None:
    """Execute a scheduled agent job: create AiSession → submit initial prompt → update run metadata."""
    from app.ai_console.gateway import get_gateway
    from app.db import get_session
    from app.models import AiSession, AiTurn, ScheduledAgentJob
    from app.services.ids import new_hash_id
    from sqlmodel import select

    with get_session() as db:
        job = db.exec(
            select(ScheduledAgentJob).where(ScheduledAgentJob.id == job_id)
        ).first()
        if job is None or not job.enabled:
            return

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
                select(ScheduledAgentJob).where(ScheduledAgentJob.enabled == True)
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
