"""Evolve router — self-improvement loop for MissionControl.

mc evolve seed --spec <spec.json>  → seeds an evolve mission + task backlog
mc evolve run  --mission <id>      → launches an agent against the evolve mission
mc evolve status --mission <id>    → returns progress
"""
import datetime
import json
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlmodel import select

from app.db import get_session
from app.models import EvolveMission, EvolveRun
from app.services.authz import actor_subject_from_request

router = APIRouter(prefix="/evolve", tags=["evolve"])


class EvolveSpec(BaseModel):
    spec: dict[str, Any]


class EvolveRunRequest(BaseModel):
    agent: str = "claude"


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
    """Launch an agent against a seeded evolve mission."""
    owner_subject = actor_subject_from_request(request)
    with get_session() as db:
        mission = db.exec(
            select(EvolveMission)
            .where(EvolveMission.mission_id == mission_id)
            .where(EvolveMission.owner_subject == owner_subject)
        ).first()
        if mission is None:
            raise HTTPException(status_code=404, detail=f"evolve mission {mission_id} not found")

        now = datetime.datetime.utcnow()
        run_id = f"run-{uuid.uuid4().hex[:8]}"
        run_record = EvolveRun(
            run_id=run_id,
            mission_id=mission_id,
            owner_subject=owner_subject,
            agent=body.agent,
            status="launched",
            started_at=now,
        )
        db.add(run_record)
        mission.status = "running"
        mission.updated_at = now
        db.add(mission)
        db.commit()

    return {
        "mission_id": mission_id,
        "run_id": run_id,
        "agent": body.agent,
        "status": "launched",
        "started_at": now.isoformat() + "Z",
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
