"""Scheduled agent jobs router — CRUD for ScheduledAgentJob records."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlmodel import select

from app.ai_console.runtime_config import normalize_runtime_kind
from app.db import get_session
from app.models import ScheduledAgentJob
from app.services.agent_scheduler import _run_job, schedule_job, unschedule_job
from app.services.authz import actor_subject_from_request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/scheduled-jobs", tags=["scheduled-jobs"])


class ScheduledJobCreate(BaseModel):
    name: str
    description: str = ""
    cron_expr: str
    runtime_kind: str = ""
    initial_prompt: str
    system_context: Optional[str] = None
    policy: dict = Field(default_factory=dict)
    enabled: bool = True


class ScheduledJobUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    cron_expr: Optional[str] = None
    runtime_kind: Optional[str] = None
    initial_prompt: Optional[str] = None
    system_context: Optional[str] = None
    policy: Optional[dict] = None
    enabled: Optional[bool] = None


def _serialize(job: ScheduledAgentJob) -> dict[str, Any]:
    return {
        "id": job.id,
        "owner_subject": job.owner_subject,
        "name": job.name,
        "description": job.description,
        "cron_expr": job.cron_expr,
        "runtime_kind": job.runtime_kind,
        "initial_prompt": job.initial_prompt,
        "system_context": job.system_context,
        "policy": json.loads(job.policy_json or "{}"),
        "enabled": job.enabled,
        "last_run_at": job.last_run_at.isoformat() + "Z" if job.last_run_at else None,
        "last_session_id": job.last_session_id,
        "created_at": job.created_at.isoformat() + "Z",
        "updated_at": job.updated_at.isoformat() + "Z",
    }


@router.get("")
def list_scheduled_jobs(request: Request) -> list[dict[str, Any]]:
    owner = actor_subject_from_request(request)
    with get_session() as db:
        jobs = db.exec(
            select(ScheduledAgentJob).where(ScheduledAgentJob.owner_subject == owner)
        ).all()
    return [_serialize(j) for j in jobs]


@router.post("")
def create_scheduled_job(body: ScheduledJobCreate, request: Request) -> dict[str, Any]:
    owner = actor_subject_from_request(request)
    now = datetime.utcnow()
    job = ScheduledAgentJob(
        owner_subject=owner,
        name=body.name,
        description=body.description,
        cron_expr=body.cron_expr,
        runtime_kind=normalize_runtime_kind(body.runtime_kind).value,
        initial_prompt=body.initial_prompt,
        system_context=body.system_context,
        policy_json=json.dumps(body.policy, separators=(",", ":")),
        enabled=body.enabled,
        created_at=now,
        updated_at=now,
    )
    with get_session() as db:
        db.add(job)
        db.commit()
        db.refresh(job)
        job_id = job.id
        cron_expr = job.cron_expr
        enabled = job.enabled

    if enabled:
        try:
            schedule_job(job_id, cron_expr)
        except Exception as exc:
            logger.warning("Failed to schedule job %s: %s", job_id, exc)

    with get_session() as db:
        job = db.exec(select(ScheduledAgentJob).where(ScheduledAgentJob.id == job_id)).first()
    return _serialize(job)


@router.get("/{job_id}")
def get_scheduled_job(job_id: int, request: Request) -> dict[str, Any]:
    owner = actor_subject_from_request(request)
    with get_session() as db:
        job = db.exec(
            select(ScheduledAgentJob)
            .where(ScheduledAgentJob.id == job_id)
            .where(ScheduledAgentJob.owner_subject == owner)
        ).first()
    if job is None:
        raise HTTPException(status_code=404, detail=f"scheduled job {job_id} not found")
    return _serialize(job)


@router.put("/{job_id}")
def update_scheduled_job(job_id: int, body: ScheduledJobUpdate, request: Request) -> dict[str, Any]:
    owner = actor_subject_from_request(request)
    with get_session() as db:
        job = db.exec(
            select(ScheduledAgentJob)
            .where(ScheduledAgentJob.id == job_id)
            .where(ScheduledAgentJob.owner_subject == owner)
        ).first()
        if job is None:
            raise HTTPException(status_code=404, detail=f"scheduled job {job_id} not found")

        if body.name is not None:
            job.name = body.name
        if body.description is not None:
            job.description = body.description
        if body.cron_expr is not None:
            job.cron_expr = body.cron_expr
        if body.runtime_kind is not None:
            job.runtime_kind = normalize_runtime_kind(body.runtime_kind).value
        if body.initial_prompt is not None:
            job.initial_prompt = body.initial_prompt
        if body.system_context is not None:
            job.system_context = body.system_context
        if body.policy is not None:
            job.policy_json = json.dumps(body.policy, separators=(",", ":"))
        if body.enabled is not None:
            job.enabled = body.enabled

        job.updated_at = datetime.utcnow()
        db.add(job)
        db.commit()
        db.refresh(job)
        job_id_out = job.id
        cron_expr = job.cron_expr
        enabled = job.enabled

    if enabled:
        try:
            schedule_job(job_id_out, cron_expr)
        except Exception as exc:
            logger.warning("Failed to reschedule job %s: %s", job_id_out, exc)
    else:
        unschedule_job(job_id_out)

    with get_session() as db:
        job = db.exec(select(ScheduledAgentJob).where(ScheduledAgentJob.id == job_id_out)).first()
    return _serialize(job)


@router.delete("/{job_id}")
def delete_scheduled_job(job_id: int, request: Request) -> dict[str, Any]:
    owner = actor_subject_from_request(request)
    with get_session() as db:
        job = db.exec(
            select(ScheduledAgentJob)
            .where(ScheduledAgentJob.id == job_id)
            .where(ScheduledAgentJob.owner_subject == owner)
        ).first()
        if job is None:
            raise HTTPException(status_code=404, detail=f"scheduled job {job_id} not found")
        unschedule_job(job_id)
        db.delete(job)
        db.commit()
    return {"deleted": True, "id": job_id}


@router.post("/{job_id}/run")
async def run_scheduled_job_now(job_id: int, request: Request) -> dict[str, Any]:
    """Trigger a scheduled job immediately (one-shot, non-blocking)."""
    owner = actor_subject_from_request(request)
    with get_session() as db:
        job = db.exec(
            select(ScheduledAgentJob)
            .where(ScheduledAgentJob.id == job_id)
            .where(ScheduledAgentJob.owner_subject == owner)
        ).first()
        if job is None:
            raise HTTPException(status_code=404, detail=f"scheduled job {job_id} not found")

    asyncio.create_task(_run_job(job_id))
    return {"triggered": True, "id": job_id}
