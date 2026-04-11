"""
Run coordinator — manages agent run lifecycle.
All operations are scoped by owner_subject.
Enforcement principle: this service does NO per-call HTTP.
Budget enforcement hooks will be wired in Phase 3.
"""
import json
import uuid
from datetime import datetime
from typing import Optional, List, Literal

import sqlalchemy.exc
from sqlmodel import Session, select

from app.db import engine
from app.models import AgentRun, RunCheckpoint

RunStatus = Literal[
    "starting", "running", "paused", "waiting_review", "waiting_budget",
    "completed", "failed", "cancelled"
]

TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


def start_run(
    owner_subject: str,
    runtime_kind: str,
    agent_id: Optional[str] = None,
    task_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    runtime_session_id: Optional[str] = None,
) -> AgentRun:
    """
    Allocate a new AgentRun. If idempotency_key is provided and a run with
    that key already exists for this owner, return the existing run (idempotent).
    """
    with Session(engine) as session:
        if idempotency_key:
            existing = session.exec(
                select(AgentRun).where(
                    AgentRun.owner_subject == owner_subject,
                    AgentRun.idempotency_key == idempotency_key,
                )
            ).first()
            if existing:
                return existing

        run = AgentRun(
            id=str(uuid.uuid4()),
            owner_subject=owner_subject,
            runtime_kind=runtime_kind,
            mesh_agent_id=agent_id,
            mesh_task_id=task_id,
            runtime_session_id=runtime_session_id,
            idempotency_key=idempotency_key,
            status="starting",
            resume_token=str(uuid.uuid4()),
            started_at=datetime.utcnow(),
            total_cost_cents=0,
        )
        session.add(run)
        session.commit()
        session.refresh(run)
        return run


def checkpoint(
    run_id: str,
    kind: Literal["tool_call", "turn", "review", "publish", "manual"],
    payload: dict,
    owner_subject: str,
) -> RunCheckpoint:
    """Append a checkpoint to the run. Increments seq monotonically.

    Retries up to 3 times on UniqueConstraint violations (concurrent writers).
    """
    max_retries = 3
    for attempt in range(max_retries):
        with Session(engine) as session:
            existing = session.exec(
                select(RunCheckpoint)
                .where(RunCheckpoint.run_id == run_id)
                .order_by(RunCheckpoint.seq.desc())
            ).first()
            next_seq = (existing.seq + 1) if existing else 0

            cp = RunCheckpoint(
                id=str(uuid.uuid4()),
                run_id=run_id,
                seq=next_seq,
                kind=kind,
                payload_json=json.dumps(payload),
                created_at=datetime.utcnow(),
            )
            session.add(cp)
            try:
                session.commit()
                session.refresh(cp)
                return cp
            except sqlalchemy.exc.IntegrityError:
                session.rollback()
                if attempt == max_retries - 1:
                    raise
                continue


def resume(run_id: str, resume_token: str, owner_subject: str) -> Optional[dict]:
    """
    Verify resume_token and return last checkpoint + event count.
    Returns dict with: run, last_checkpoint (or None), resume_token.
    Returns None if run not found, {"error": "invalid_resume_token"} on bad token.
    """
    with Session(engine) as session:
        run = session.exec(
            select(AgentRun).where(
                AgentRun.id == run_id,
                AgentRun.owner_subject == owner_subject,
            )
        ).first()
        if not run:
            return None
        if run.resume_token != resume_token:
            return {"error": "invalid_resume_token"}

        last_cp = session.exec(
            select(RunCheckpoint)
            .where(RunCheckpoint.run_id == run_id)
            .order_by(RunCheckpoint.seq.desc())
        ).first()

        return {
            "run": run,
            "last_checkpoint": last_cp,
            "resume_token": resume_token,
        }


def transition(
    run_id: str,
    new_status: RunStatus,
    owner_subject: str,
    reason: Optional[str] = None,
) -> AgentRun:
    """Transition run to new status. Updates ended_at for terminal statuses."""
    with Session(engine) as session:
        run = session.exec(
            select(AgentRun).where(
                AgentRun.id == run_id,
                AgentRun.owner_subject == owner_subject,
            )
        ).first()
        if not run:
            raise ValueError(f"Run {run_id} not found")

        run.status = new_status
        if new_status in TERMINAL_STATUSES:
            run.ended_at = datetime.utcnow()

        session.add(run)
        session.commit()
        session.refresh(run)
        return run


def get_run(run_id: str, owner_subject: str) -> Optional[AgentRun]:
    with Session(engine) as session:
        return session.exec(
            select(AgentRun).where(
                AgentRun.id == run_id,
                AgentRun.owner_subject == owner_subject,
            )
        ).first()


def list_runs(
    owner_subject: str,
    agent_id: Optional[str] = None,
    task_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
) -> List[AgentRun]:
    with Session(engine) as session:
        q = select(AgentRun).where(AgentRun.owner_subject == owner_subject)
        if agent_id:
            q = q.where(AgentRun.mesh_agent_id == agent_id)
        if task_id:
            q = q.where(AgentRun.mesh_task_id == task_id)
        if status:
            q = q.where(AgentRun.status == status)
        q = q.order_by(AgentRun.started_at.desc()).limit(limit)
        return list(session.exec(q).all())


def list_checkpoints(run_id: str, owner_subject: str) -> List[RunCheckpoint]:
    """List all checkpoints for a run, verifying owner access."""
    with Session(engine) as session:
        run = session.exec(
            select(AgentRun).where(
                AgentRun.id == run_id,
                AgentRun.owner_subject == owner_subject,
            )
        ).first()
        if not run:
            return []
        return list(
            session.exec(
                select(RunCheckpoint)
                .where(RunCheckpoint.run_id == run_id)
                .order_by(RunCheckpoint.seq)
            ).all()
        )
