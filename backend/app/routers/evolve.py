"""Evolve router — self-improvement loop for MissionControl.

mc evolve seed --spec <spec.json>  → seeds an evolve mission + task backlog
mc evolve run  --mission <id>      → launches an agent against the evolve mission
mc evolve status --mission <id>    → returns progress
"""
from __future__ import annotations

import asyncio
import datetime
import json
import logging
import uuid
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlmodel import select

from app.db import get_session
from app.models import AiSession, AiTurn, EvolveMission, EvolveRun
from app.services.authz import actor_subject_from_request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/evolve", tags=["evolve"])

# Service principal used for agent-driven evolve sessions
EVOLVE_BOT_SUBJECT = "missioncontrol-bot"


class EvolveSpec(BaseModel):
    spec: dict[str, Any]


class EvolveRunRequest(BaseModel):
    runtime_kind: str = "opencode"
    policy: dict = {}


async def _run_evolve_turn(
    session_id: str,
    prompt: str,
    owner_subject: str,
    run_id: str,
    mission_id: str,
) -> None:
    """Background task: submit initial turn and update run/mission status on completion."""
    from app.ai_console.gateway import get_gateway

    try:
        with get_session() as db:
            session_row = db.exec(
                select(AiSession).where(AiSession.id == session_id)
            ).first()
            if session_row is None:
                return

            user_turn = AiTurn(
                session_id=session_id,
                role="user",
                content_json=json.dumps({"text": prompt}, separators=(",", ":")),
                created_at=datetime.datetime.utcnow(),
            )
            db.add(user_turn)
            db.commit()
            db.refresh(user_turn)

            gateway = get_gateway()
            await gateway.process_turn(
                db=db,
                session_row=session_row,
                user_turn=user_turn,
                message=prompt,
                subject=owner_subject,
            )

            # Mark run and mission completed
            run = db.exec(select(EvolveRun).where(EvolveRun.run_id == run_id)).first()
            if run is not None:
                run.status = "completed"
                db.add(run)

            mission = db.exec(
                select(EvolveMission).where(EvolveMission.mission_id == mission_id)
            ).first()
            if mission is not None:
                mission.status = "completed"
                mission.updated_at = datetime.datetime.utcnow()
                db.add(mission)

            db.commit()
            logger.info("evolve_run_complete run_id=%s session_id=%s", run_id, session_id)

    except Exception as exc:
        logger.error("evolve_run_error run_id=%s error=%s", run_id, exc)
        try:
            with get_session() as db:
                run = db.exec(select(EvolveRun).where(EvolveRun.run_id == run_id)).first()
                if run is not None:
                    run.status = "failed"
                    db.add(run)
                    db.commit()
        except Exception:
            pass


@router.post("/missions")
async def seed_evolve_mission(body: EvolveSpec, request: Request) -> dict[str, Any]:
    """Seed an evolve mission from a spec dict.

    The spec may contain: name, description, tasks (list of task dicts),
    agent_system_prompt, and scoring_criteria.
    """
    mission_id = f"evolve-{uuid.uuid4().hex[:8]}"
    now = datetime.datetime.utcnow()
    record = EvolveMission(
        mission_id=mission_id,
        owner_subject=actor_subject_from_request(request),
        status="seeded",
        spec_json=json.dumps(body.spec),
        created_at=now,
        updated_at=now,
    )
    with get_session() as db:
        db.add(record)
        db.commit()

    return {
        "mission_id": mission_id,
        "status": "seeded",
        "created_at": now.isoformat() + "Z",
        "task_count": len(body.spec.get("tasks", [])),
    }


@router.post("/missions/{mission_id}/run")
async def run_evolve_mission(
    mission_id: str, body: EvolveRunRequest, request: Request
) -> dict[str, Any]:
    """Launch an opencode agent against a seeded evolve mission.

    Creates an AiSession via ConsoleGateway, creates an EvolveRun linked to it,
    then submits the mission spec as the first turn in the background.
    """
    owner_subject = actor_subject_from_request(request)

    # Verify mission exists and capture spec
    with get_session() as db:
        mission = db.exec(
            select(EvolveMission)
            .where(EvolveMission.mission_id == mission_id)
            .where(EvolveMission.owner_subject == owner_subject)
        ).first()
        if mission is None:
            raise HTTPException(status_code=404, detail=f"evolve mission {mission_id} not found")
        spec_json = mission.spec_json

    from app.ai_console.gateway import get_gateway
    from app.services.ids import new_hash_id

    gateway = get_gateway()
    session_id = new_hash_id()
    run_id = f"run-{uuid.uuid4().hex[:8]}"
    now = datetime.datetime.utcnow()

    # Create AiSession + EvolveRun in one db block
    with get_session() as db:
        ai_session = await gateway.create_session(
            db=db,
            subject=EVOLVE_BOT_SUBJECT,
            session_id=session_id,
            title=f"Evolve: {mission_id}",
            runtime_kind=body.runtime_kind,
            policy_dict={"require_approval_for_writes": True, **body.policy},
        )

        run_record = EvolveRun(
            run_id=run_id,
            mission_id=mission_id,
            owner_subject=owner_subject,
            agent=body.runtime_kind,
            status="running",
            started_at=now,
            ai_session_id=ai_session.id,
        )
        db.add(run_record)

        mission_row = db.exec(
            select(EvolveMission).where(EvolveMission.mission_id == mission_id)
        ).first()
        if mission_row:
            mission_row.status = "running"
            mission_row.updated_at = now
            db.add(mission_row)

        db.commit()
        created_session_id = ai_session.id  # capture before session expires

    # Submit initial turn in background — don't block the HTTP response
    spec = json.loads(spec_json or "{}")
    prompt = f"Execute evolve mission '{mission_id}':\n{json.dumps(spec, indent=2)}"
    asyncio.create_task(_run_evolve_turn(created_session_id, prompt, owner_subject, run_id, mission_id))

    return {
        "mission_id": mission_id,
        "run_id": run_id,
        "agent": body.runtime_kind,
        "status": "running",
        "started_at": now.isoformat() + "Z",
        "ai_session_id": created_session_id,
    }


@router.get("/missions/{mission_id}/status")
async def get_evolve_status(mission_id: str, request: Request) -> dict[str, Any]:
    """Return the current status of an evolve mission."""
    owner_subject = actor_subject_from_request(request)
    with get_session() as db:
        mission = db.exec(
            select(EvolveMission)
            .where(EvolveMission.mission_id == mission_id)
            .where(EvolveMission.owner_subject == owner_subject)
        ).first()
        if mission is None:
            raise HTTPException(status_code=404, detail=f"evolve mission {mission_id} not found")
        spec = json.loads(mission.spec_json or "{}")
        runs = db.exec(
            select(EvolveRun)
            .where(EvolveRun.mission_id == mission_id)
            .where(EvolveRun.owner_subject == owner_subject)
        ).all()
        run_items = [
            {
                "run_id": run.run_id,
                "agent": run.agent,
                "started_at": run.started_at.isoformat() + "Z",
                "status": run.status,
                "ai_session_id": run.ai_session_id,
            }
            for run in runs
        ]

    return {
        "mission_id": mission_id,
        "status": mission.status,
        "created_at": mission.created_at.isoformat() + "Z",
        "task_count": len(spec.get("tasks", [])),
        "run_count": len(run_items),
        "runs": run_items,
    }
