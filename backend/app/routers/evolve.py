"""Evolve router — self-improvement loop for MissionControl.

mc evolve seed --spec <spec.json>  → seeds an evolve mission + task backlog
mc evolve run  --mission <id>      → launches an agent against the evolve mission
mc evolve status --mission <id>    → returns progress
"""
import datetime
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(prefix="/evolve", tags=["evolve"])


class EvolveSpec(BaseModel):
    spec: dict[str, Any]


class EvolveRunRequest(BaseModel):
    agent: str = "claude"


# In-memory store for seeded missions (replace with DB persistence when ready)
_EVOLVE_MISSIONS: dict[str, dict[str, Any]] = {}


@router.post("/missions")
async def seed_evolve_mission(body: EvolveSpec, request: Request) -> dict[str, Any]:
    """Seed an evolve mission from a spec dict.

    The spec may contain: name, description, tasks (list of task dicts),
    agent_system_prompt, and scoring_criteria.
    """
    mission_id = f"evolve-{uuid.uuid4().hex[:8]}"
    created_at = datetime.datetime.utcnow().isoformat() + "Z"
    record = {
        "mission_id": mission_id,
        "status": "seeded",
        "spec": body.spec,
        "created_at": created_at,
        "runs": [],
    }
    _EVOLVE_MISSIONS[mission_id] = record
    return {
        "mission_id": mission_id,
        "status": "seeded",
        "created_at": created_at,
        "task_count": len(body.spec.get("tasks", [])),
    }


@router.post("/missions/{mission_id}/run")
async def run_evolve_mission(
    mission_id: str, body: EvolveRunRequest, request: Request
) -> dict[str, Any]:
    """Launch an agent against a seeded evolve mission."""
    mission = _EVOLVE_MISSIONS.get(mission_id)
    if mission is None:
        raise HTTPException(status_code=404, detail=f"evolve mission {mission_id} not found")
    run_id = f"run-{uuid.uuid4().hex[:8]}"
    started_at = datetime.datetime.utcnow().isoformat() + "Z"
    run_record = {"run_id": run_id, "agent": body.agent, "started_at": started_at, "status": "launched"}
    mission["runs"].append(run_record)
    mission["status"] = "running"
    return {
        "mission_id": mission_id,
        "run_id": run_id,
        "agent": body.agent,
        "status": "launched",
        "started_at": started_at,
    }


@router.get("/missions/{mission_id}/status")
async def get_evolve_status(mission_id: str, request: Request) -> dict[str, Any]:
    """Return the current status of an evolve mission."""
    mission = _EVOLVE_MISSIONS.get(mission_id)
    if mission is None:
        raise HTTPException(status_code=404, detail=f"evolve mission {mission_id} not found")
    return {
        "mission_id": mission_id,
        "status": mission["status"],
        "created_at": mission["created_at"],
        "task_count": len(mission["spec"].get("tasks", [])),
        "run_count": len(mission["runs"]),
        "runs": mission["runs"],
    }
