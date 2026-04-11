"""REST endpoints for AgentRun lifecycle management."""
from typing import Optional
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.services.authz import actor_subject_from_request
from app.services import run_coordinator

router = APIRouter(prefix="/runs", tags=["runs"])


def _run_payload(run) -> dict:
    return {
        "id": run.id,
        "owner_subject": run.owner_subject,
        "runtime_kind": run.runtime_kind,
        "runtime_session_id": run.runtime_session_id,
        "status": run.status,
        "mesh_agent_id": run.mesh_agent_id,
        "mesh_task_id": run.mesh_task_id,
        "resume_token": run.resume_token,
        "started_at": run.started_at,
        "ended_at": run.ended_at,
        "last_checkpoint_at": run.last_checkpoint_at,
        "total_cost_cents": run.total_cost_cents,
        "metadata_json": run.metadata_json,
    }


def _checkpoint_payload(cp) -> dict:
    return {
        "id": cp.id,
        "run_id": cp.run_id,
        "seq": cp.seq,
        "kind": cp.kind,
        "payload_json": cp.payload_json,
        "created_at": cp.created_at,
    }


class StartRunRequest(BaseModel):
    runtime_kind: str
    agent_id: Optional[str] = None
    task_id: Optional[str] = None
    idempotency_key: Optional[str] = None
    runtime_session_id: Optional[str] = None


class CheckpointRequest(BaseModel):
    kind: str
    payload: dict = {}


class ResumeBody(BaseModel):
    resume_token: str


@router.get("")
def list_runs(
    request: Request,
    agent_id: Optional[str] = None,
    task_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
):
    owner = actor_subject_from_request(request)
    runs = run_coordinator.list_runs(
        owner_subject=owner,
        agent_id=agent_id,
        task_id=task_id,
        status=status,
        limit=max(1, min(limit, 200)),
    )
    return [_run_payload(r) for r in runs]


@router.post("")
def start_run(request: Request, body: StartRunRequest):
    owner = actor_subject_from_request(request)
    run = run_coordinator.start_run(
        owner_subject=owner,
        runtime_kind=body.runtime_kind,
        agent_id=body.agent_id,
        task_id=body.task_id,
        idempotency_key=body.idempotency_key,
        runtime_session_id=body.runtime_session_id,
    )
    return _run_payload(run)


@router.get("/{run_id}")
def get_run(run_id: str, request: Request):
    owner = actor_subject_from_request(request)
    run = run_coordinator.get_run(run_id, owner)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return _run_payload(run)


@router.post("/{run_id}/pause")
def pause_run(run_id: str, request: Request):
    owner = actor_subject_from_request(request)
    try:
        run = run_coordinator.transition(run_id, "paused", owner)
    except ValueError:
        raise HTTPException(status_code=404, detail="Run not found")
    return _run_payload(run)


@router.post("/{run_id}/cancel")
def cancel_run(run_id: str, request: Request):
    owner = actor_subject_from_request(request)
    try:
        run = run_coordinator.transition(run_id, "cancelled", owner)
    except ValueError:
        raise HTTPException(status_code=404, detail="Run not found")
    return _run_payload(run)


@router.post("/{run_id}/resume")
def resume_run(run_id: str, body: ResumeBody, request: Request):
    owner = actor_subject_from_request(request)
    result = run_coordinator.resume(run_id, body.resume_token, owner)
    if result is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if "error" in result:
        raise HTTPException(status_code=403, detail=result["error"])
    return result


@router.get("/{run_id}/checkpoints")
def list_checkpoints(run_id: str, request: Request):
    owner = actor_subject_from_request(request)
    cps = run_coordinator.list_checkpoints(run_id, owner)
    return [_checkpoint_payload(cp) for cp in cps]


@router.post("/{run_id}/checkpoint")
def create_checkpoint(run_id: str, request: Request, body: CheckpointRequest):
    owner = actor_subject_from_request(request)
    # Verify run ownership before writing checkpoint
    run = run_coordinator.get_run(run_id, owner)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    cp = run_coordinator.checkpoint(
        run_id=run_id,
        kind=body.kind,
        payload=body.payload,
        owner_subject=owner,
    )
    return _checkpoint_payload(cp)
