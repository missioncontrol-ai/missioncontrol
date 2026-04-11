"""mc-mesh work model endpoints.

Covers:
  - Kluster-scoped task DAGs (MeshTask)
  - Mission-scoped durable agent pool (MeshAgent)
  - Typed progress events (MeshProgressEvent)
  - Inter-agent messages (MeshMessage)
  - Task artifact linkage (MeshTaskArtifact)
  - Live streaming via websocket (per-kluster and per-mission feeds)
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field as PField
from sqlmodel import select

from app.db import get_session
from app.models import (
    Artifact,
    MeshAgent,
    MeshMessage,
    MeshProgressEvent,
    MeshTask,
    MeshTaskArtifact,
)
from app.services.authz import actor_subject_from_request

router = APIRouter(prefix="/work", tags=["work"])

# ---------------------------------------------------------------------------
# Default task lease TTL
# ---------------------------------------------------------------------------
LEASE_TTL_SECONDS = 120


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class MeshTaskCreate(BaseModel):
    title: str
    description: str = ""
    input_json: str = "{}"
    claim_policy: str = "first_claim"  # assigned | first_claim | broadcast
    depends_on: list[str] = PField(default_factory=list)
    produces: dict = PField(default_factory=dict)
    consumes: dict = PField(default_factory=dict)
    required_capabilities: list[str] = PField(default_factory=list)
    priority: int = 0
    parent_task_id: Optional[str] = None


class MeshTaskRead(BaseModel):
    id: str
    kluster_id: str
    mission_id: str
    parent_task_id: Optional[str]
    title: str
    description: str
    claim_policy: str
    depends_on: list
    produces: dict
    consumes: dict
    required_capabilities: list
    status: str
    claimed_by_agent_id: Optional[str]
    result_artifact_id: Optional[str]
    priority: int
    lease_expires_at: Optional[datetime]
    created_by_subject: str
    created_at: datetime
    updated_at: datetime


class MeshAgentEnroll(BaseModel):
    runtime_kind: str  # claude_code | codex | gemini | custom
    runtime_version: str = ""
    capabilities: list[str] = PField(default_factory=list)
    labels: dict = PField(default_factory=dict)
    node_id: Optional[str] = None


class MeshAgentRead(BaseModel):
    id: str
    mission_id: str
    node_id: Optional[str]
    runtime_kind: str
    runtime_version: str
    capabilities: list
    labels: dict
    status: str
    current_task_id: Optional[str]
    enrolled_at: datetime
    last_heartbeat_at: Optional[datetime]


class MeshProgressEventCreate(BaseModel):
    event_type: str
    phase: Optional[str] = None
    step: Optional[str] = None
    summary: str = ""
    payload_json: str = "{}"


class MeshMessageCreate(BaseModel):
    to_agent_id: Optional[str] = None
    task_id: Optional[str] = None
    channel: str = "coordination"
    body_json: str = "{}"
    in_reply_to: Optional[int] = None


class TaskClaimResult(BaseModel):
    task_id: str
    lease_expires_at: datetime


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _task_to_read(t: MeshTask) -> dict:
    return {
        "id": t.id,
        "kluster_id": t.kluster_id,
        "mission_id": t.mission_id,
        "parent_task_id": t.parent_task_id,
        "title": t.title,
        "description": t.description,
        "claim_policy": t.claim_policy,
        "depends_on": json.loads(t.depends_on or "[]"),
        "produces": json.loads(t.produces or "{}"),
        "consumes": json.loads(t.consumes or "{}"),
        "required_capabilities": json.loads(t.required_capabilities or "[]"),
        "status": t.status,
        "claimed_by_agent_id": t.claimed_by_agent_id,
        "result_artifact_id": t.result_artifact_id,
        "priority": t.priority,
        "lease_expires_at": t.lease_expires_at,
        "created_by_subject": t.created_by_subject,
        "created_at": t.created_at,
        "updated_at": t.updated_at,
    }


def _agent_to_read(a: MeshAgent) -> dict:
    return {
        "id": a.id,
        "mission_id": a.mission_id,
        "node_id": a.node_id,
        "runtime_kind": a.runtime_kind,
        "runtime_version": a.runtime_version,
        "capabilities": json.loads(a.capabilities or "[]"),
        "labels": json.loads(a.labels or "{}"),
        "status": a.status,
        "current_task_id": a.current_task_id,
        "enrolled_at": a.enrolled_at,
        "last_heartbeat_at": a.last_heartbeat_at,
    }


# ---------------------------------------------------------------------------
# Kluster task DAG endpoints
# ---------------------------------------------------------------------------


@router.post("/klusters/{kluster_id}/tasks", status_code=201)
def create_task(kluster_id: str, body: MeshTaskCreate, request: Request):
    subject = actor_subject_from_request(request)
    # Derive mission_id from the kluster (Kluster.mission_id may be null for
    # standalone klusters; store empty string in that case).
    from app.models import Kluster

    with get_session() as session:
        kluster = session.get(Kluster, kluster_id)
        if kluster is None:
            raise HTTPException(status_code=404, detail="kluster not found")
        mission_id = kluster.mission_id or ""

        # Validate depends_on IDs exist in this kluster
        for dep_id in body.depends_on:
            dep = session.get(MeshTask, dep_id)
            if dep is None or dep.kluster_id != kluster_id:
                raise HTTPException(
                    status_code=400,
                    detail=f"depends_on task {dep_id!r} not found in this kluster",
                )

        # Cycle detection — reject if adding these edges creates a cycle
        new_id = str(uuid.uuid4())
        if body.depends_on and _detect_cycle(kluster_id, new_id, body.depends_on, session):
            raise HTTPException(status_code=400, detail="depends_on would create a cycle")

        # Validate claim_policy
        valid_policies = {"assigned", "first_claim", "broadcast"}
        if body.claim_policy not in valid_policies:
            raise HTTPException(
                status_code=400,
                detail=f"claim_policy must be one of {valid_policies}",
            )

        # Compute initial status: pending unless all deps resolved (empty deps = ready)
        initial_status = "ready" if not body.depends_on else "pending"

        task = MeshTask(
            id=new_id,
            kluster_id=kluster_id,
            mission_id=mission_id,
            parent_task_id=body.parent_task_id,
            title=body.title,
            description=body.description,
            input_json=body.input_json,
            claim_policy=body.claim_policy,
            depends_on=json.dumps(body.depends_on),
            produces=json.dumps(body.produces),
            consumes=json.dumps(body.consumes),
            required_capabilities=json.dumps(body.required_capabilities),
            status=initial_status,
            priority=body.priority,
            created_by_subject=subject,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        session.add(task)
        session.commit()
        session.refresh(task)
        return _task_to_read(task)


@router.get("/klusters/{kluster_id}/tasks")
def list_tasks(kluster_id: str, status: Optional[str] = None):
    with get_session() as session:
        _expire_stale_leases(session, kluster_id)
        session.commit()
        q = select(MeshTask).where(MeshTask.kluster_id == kluster_id)
        if status:
            q = q.where(MeshTask.status == status)
        q = q.order_by(MeshTask.priority.desc(), MeshTask.created_at)
        tasks = session.exec(q).all()
        return [_task_to_read(t) for t in tasks]


@router.get("/klusters/{kluster_id}/graph")
def task_graph(kluster_id: str):
    """Return all tasks as a DAG: {nodes: [...], edges: [...]}."""
    with get_session() as session:
        tasks = session.exec(
            select(MeshTask).where(MeshTask.kluster_id == kluster_id)
        ).all()
        nodes = [_task_to_read(t) for t in tasks]
        edges = []
        for t in tasks:
            for dep_id in json.loads(t.depends_on or "[]"):
                edges.append({"from": dep_id, "to": t.id})
        return {"nodes": nodes, "edges": edges}


@router.get("/tasks/{task_id}")
def get_task(task_id: str):
    with get_session() as session:
        task = session.get(MeshTask, task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        return _task_to_read(task)


@router.post("/tasks/{task_id}/cancel")
def cancel_task(task_id: str, request: Request):
    with get_session() as session:
        task = session.get(MeshTask, task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        if task.status in ("finished", "cancelled"):
            raise HTTPException(status_code=409, detail=f"task already {task.status}")
        task.status = "cancelled"
        task.updated_at = datetime.utcnow()
        session.add(task)
        session.commit()
        return {"status": "cancelled"}


@router.post("/tasks/{task_id}/retry")
def retry_task(task_id: str, request: Request):
    with get_session() as session:
        task = session.get(MeshTask, task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        if task.status not in ("failed", "cancelled"):
            raise HTTPException(status_code=409, detail="only failed/cancelled tasks can be retried")
        task.status = "ready" if not json.loads(task.depends_on or "[]") else "pending"
        task.claimed_by_agent_id = None
        task.lease_expires_at = None
        task.updated_at = datetime.utcnow()
        session.add(task)
        session.commit()
        return _task_to_read(task)


@router.post("/tasks/{task_id}/claim")
def claim_task(task_id: str, request: Request):
    """Agent claims a ready task; returns a lease expiry.

    Broadcast tasks (claim_policy=broadcast) allow concurrent claims from
    multiple agents — the task transitions to running and stays claimable.
    First-claim and assigned tasks use exclusive ownership.
    """
    subject = actor_subject_from_request(request)
    with get_session() as session:
        task = session.get(MeshTask, task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")

        is_broadcast = task.claim_policy == "broadcast"

        if is_broadcast:
            # Broadcast: allow any agent to claim while ready or running.
            if task.status not in ("ready", "running"):
                raise HTTPException(
                    status_code=409, detail=f"broadcast task is {task.status}"
                )
        else:
            # Expire stale leases before checking status so we don't block on
            # a crashed agent's leftover lease.
            _expire_stale_leases(session, task.kluster_id)
            task = session.get(MeshTask, task_id)  # re-fetch after potential mutation
            if task.status != "ready":
                raise HTTPException(status_code=409, detail=f"task is {task.status}, not ready")

        expires_at = datetime.utcnow() + timedelta(seconds=LEASE_TTL_SECONDS)
        # For broadcast, keep status=running and don't overwrite claimed_by.
        # For exclusive claim, set status=claimed and record agent.
        if is_broadcast:
            task.status = "running"
            # claimed_by_agent_id holds the most-recent claimer; progress events
            # carry individual agent_ids for per-agent attribution.
        else:
            task.status = "claimed"
            task.claimed_by_agent_id = subject
        task.lease_expires_at = expires_at
        task.updated_at = datetime.utcnow()
        session.add(task)
        session.commit()
        return {"task_id": task_id, "lease_expires_at": expires_at}


@router.post("/tasks/{task_id}/heartbeat")
def heartbeat_task(task_id: str, request: Request):
    """Renew the task lease. Called by the agent while task is running."""
    with get_session() as session:
        task = session.get(MeshTask, task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        if task.status not in ("claimed", "running"):
            raise HTTPException(status_code=409, detail=f"task is {task.status}")
        expires_at = datetime.utcnow() + timedelta(seconds=LEASE_TTL_SECONDS)
        task.lease_expires_at = expires_at
        task.updated_at = datetime.utcnow()
        session.add(task)
        session.commit()
        return {"lease_expires_at": expires_at}


@router.post("/tasks/{task_id}/progress")
def append_progress(task_id: str, body: MeshProgressEventCreate, request: Request):
    """Agent appends a typed progress event. Triggers a wake-up for stream subscribers."""
    with get_session() as session:
        task = session.get(MeshTask, task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")

        # Set task to running on first progress event
        if task.status == "claimed":
            task.status = "running"
            task.updated_at = datetime.utcnow()
            session.add(task)

        # Monotonic seq per task
        last = session.exec(
            select(MeshProgressEvent)
            .where(MeshProgressEvent.task_id == task_id)
            .order_by(MeshProgressEvent.seq.desc())
            .limit(1)
        ).first()
        seq = (last.seq + 1) if last else 0

        # Determine agent id from the task's claimed_by or the subject
        agent_id = task.claimed_by_agent_id or actor_subject_from_request(request)

        ev = MeshProgressEvent(
            task_id=task_id,
            agent_id=agent_id,
            seq=seq,
            event_type=body.event_type,
            phase=body.phase,
            step=body.step,
            summary=body.summary,
            payload_json=body.payload_json,
            occurred_at=datetime.utcnow(),
        )
        session.add(ev)
        session.commit()
        session.refresh(ev)
        return {"id": ev.id, "seq": ev.seq, "occurred_at": ev.occurred_at}


@router.post("/tasks/{task_id}/complete")
def complete_task(task_id: str, result_artifact_id: Optional[str] = None):
    with get_session() as session:
        task = session.get(MeshTask, task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        task.status = "finished"
        task.result_artifact_id = result_artifact_id
        task.lease_expires_at = None
        task.updated_at = datetime.utcnow()
        session.add(task)

        # Unblock dependents whose all deps are now finished
        _unblock_dependents(session, task)

        session.commit()
        return _task_to_read(task)


@router.post("/tasks/{task_id}/fail")
def fail_task(task_id: str, error: str = ""):
    with get_session() as session:
        task = session.get(MeshTask, task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        task.status = "failed"
        task.lease_expires_at = None
        task.updated_at = datetime.utcnow()
        session.add(task)
        session.commit()
        return _task_to_read(task)


@router.post("/tasks/{task_id}/block")
def block_task(task_id: str, waiting_on: Optional[str] = None):
    with get_session() as session:
        task = session.get(MeshTask, task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        task.status = "blocked"
        task.updated_at = datetime.utcnow()
        session.add(task)
        session.commit()
        return _task_to_read(task)


@router.post("/tasks/{task_id}/unblock")
def unblock_task(task_id: str):
    with get_session() as session:
        task = session.get(MeshTask, task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        task.status = "ready"
        task.updated_at = datetime.utcnow()
        session.add(task)
        session.commit()
        return _task_to_read(task)


def _unblock_dependents(session, finished_task: MeshTask) -> None:
    """After a task finishes, flip any pending task to ready if all its deps are done."""
    candidates = session.exec(
        select(MeshTask)
        .where(MeshTask.kluster_id == finished_task.kluster_id)
        .where(MeshTask.status == "pending")
    ).all()

    for candidate in candidates:
        dep_ids: list = json.loads(candidate.depends_on or "[]")
        if finished_task.id not in dep_ids:
            continue
        dep_tasks = session.exec(
            select(MeshTask).where(MeshTask.id.in_(dep_ids))
        ).all()
        if all(d.status == "finished" for d in dep_tasks):
            candidate.status = "ready"
            candidate.updated_at = datetime.utcnow()
            session.add(candidate)


def _expire_stale_leases(session, kluster_id: str) -> None:
    """Return tasks whose lease has expired back to ready so they can be re-claimed."""
    now = datetime.utcnow()
    stale = session.exec(
        select(MeshTask)
        .where(MeshTask.kluster_id == kluster_id)
        .where(MeshTask.status.in_(["claimed", "running"]))
        .where(MeshTask.claim_policy != "broadcast")  # broadcast tasks don't expire
        .where(MeshTask.lease_expires_at != None)
        .where(MeshTask.lease_expires_at < now)
    ).all()
    for task in stale:
        tracing_msg = f"lease expired for task {task.id} — returning to ready"
        task.status = "ready"
        task.claimed_by_agent_id = None
        task.lease_expires_at = None
        task.updated_at = now
        session.add(task)


def _detect_cycle(kluster_id: str, new_task_id: str, depends_on: list[str], session) -> bool:
    """Return True if adding depends_on edges for new_task_id would create a cycle.

    Builds the full adjacency map (including the proposed new edges) and runs
    DFS with grey/black colouring from new_task_id.  A back-edge (grey node
    encountered while still on the DFS stack) means a cycle exists.
    """
    all_tasks = session.exec(
        select(MeshTask).where(MeshTask.kluster_id == kluster_id)
    ).all()
    adj: dict[str, list[str]] = {t.id: json.loads(t.depends_on or "[]") for t in all_tasks}
    adj[new_task_id] = depends_on  # add the proposed edges

    # DFS with 3-colour marking: 0=white, 1=grey (on stack), 2=black (done)
    color: dict[str, int] = {}

    def dfs(node: str) -> bool:
        color[node] = 1
        for neighbour in adj.get(node, []):
            state = color.get(neighbour, 0)
            if state == 1:          # back-edge → cycle
                return True
            if state == 0 and dfs(neighbour):
                return True
        color[node] = 2
        return False

    return dfs(new_task_id)


# ---------------------------------------------------------------------------
# Mission agent pool endpoints
# ---------------------------------------------------------------------------


@router.post("/missions/{mission_id}/agents/enroll", status_code=201)
def enroll_agent(mission_id: str, body: MeshAgentEnroll, request: Request):
    subject = actor_subject_from_request(request)
    with get_session() as session:
        agent = MeshAgent(
            id=str(uuid.uuid4()),
            mission_id=mission_id,
            node_id=body.node_id,
            runtime_kind=body.runtime_kind,
            runtime_version=body.runtime_version,
            capabilities=json.dumps(body.capabilities),
            labels=json.dumps(body.labels),
            status="offline",
            enrolled_by_subject=subject,
            enrolled_at=datetime.utcnow(),
        )
        session.add(agent)
        session.commit()
        session.refresh(agent)
        return _agent_to_read(agent)


@router.get("/missions/{mission_id}/agents")
def list_agents(mission_id: str, status: Optional[str] = None):
    with get_session() as session:
        q = select(MeshAgent).where(MeshAgent.mission_id == mission_id)
        if status:
            q = q.where(MeshAgent.status == status)
        agents = session.exec(q).all()
        return [_agent_to_read(a) for a in agents]


@router.post("/agents/{agent_id}/heartbeat")
def agent_heartbeat(agent_id: str):
    with get_session() as session:
        agent = session.get(MeshAgent, agent_id)
        if agent is None:
            raise HTTPException(status_code=404, detail="agent not found")
        agent.last_heartbeat_at = datetime.utcnow()
        if agent.status == "offline":
            agent.status = "idle"
        session.add(agent)
        session.commit()
        return {"status": agent.status}


@router.post("/agents/{agent_id}/status")
def set_agent_status(agent_id: str, status: str):
    valid = {"online", "busy", "idle", "offline", "errored"}
    if status not in valid:
        raise HTTPException(status_code=400, detail=f"status must be one of {valid}")
    with get_session() as session:
        agent = session.get(MeshAgent, agent_id)
        if agent is None:
            raise HTTPException(status_code=404, detail="agent not found")
        agent.status = status
        session.add(agent)
        session.commit()
        return _agent_to_read(agent)


# ---------------------------------------------------------------------------
# Inter-agent messaging
# ---------------------------------------------------------------------------


@router.post("/missions/{mission_id}/messages", status_code=201)
def send_mission_message(mission_id: str, body: MeshMessageCreate, request: Request):
    subject = actor_subject_from_request(request)
    with get_session() as session:
        msg = MeshMessage(
            mission_id=mission_id,
            kluster_id=None,
            from_agent_id=subject,
            to_agent_id=body.to_agent_id,
            task_id=body.task_id,
            channel=body.channel,
            body_json=body.body_json,
            in_reply_to=body.in_reply_to,
            created_at=datetime.utcnow(),
        )
        session.add(msg)
        session.commit()
        session.refresh(msg)
        return {"id": msg.id, "created_at": msg.created_at}


@router.post("/klusters/{kluster_id}/messages", status_code=201)
def send_kluster_message(kluster_id: str, body: MeshMessageCreate, request: Request):
    subject = actor_subject_from_request(request)
    # Derive mission_id from the kluster
    from app.models import Kluster

    with get_session() as session:
        kluster = session.get(Kluster, kluster_id)
        if kluster is None:
            raise HTTPException(status_code=404, detail="kluster not found")
        msg = MeshMessage(
            mission_id=kluster.mission_id or "",
            kluster_id=kluster_id,
            from_agent_id=subject,
            to_agent_id=body.to_agent_id,
            task_id=body.task_id,
            channel=body.channel,
            body_json=body.body_json,
            in_reply_to=body.in_reply_to,
            created_at=datetime.utcnow(),
        )
        session.add(msg)
        session.commit()
        session.refresh(msg)
        return {"id": msg.id, "created_at": msg.created_at}


@router.get("/missions/{mission_id}/messages")
def list_mission_messages(
    mission_id: str,
    channel: Optional[str] = None,
    since_id: Optional[int] = None,
):
    with get_session() as session:
        q = select(MeshMessage).where(MeshMessage.mission_id == mission_id)
        if channel:
            q = q.where(MeshMessage.channel == channel)
        if since_id:
            q = q.where(MeshMessage.id > since_id)
        q = q.order_by(MeshMessage.created_at)
        msgs = session.exec(q).all()
        return [
            {
                "id": m.id,
                "mission_id": m.mission_id,
                "kluster_id": m.kluster_id,
                "from_agent_id": m.from_agent_id,
                "to_agent_id": m.to_agent_id,
                "task_id": m.task_id,
                "channel": m.channel,
                "body_json": json.loads(m.body_json or "{}"),
                "in_reply_to": m.in_reply_to,
                "created_at": m.created_at,
                "read_at": m.read_at,
            }
            for m in msgs
        ]


@router.get("/klusters/{kluster_id}/messages")
def list_kluster_messages(
    kluster_id: str,
    channel: Optional[str] = None,
    since_id: Optional[int] = None,
):
    with get_session() as session:
        q = select(MeshMessage).where(MeshMessage.kluster_id == kluster_id)
        if channel:
            q = q.where(MeshMessage.channel == channel)
        if since_id:
            q = q.where(MeshMessage.id > since_id)
        q = q.order_by(MeshMessage.created_at)
        msgs = session.exec(q).all()
        return [
            {
                "id": m.id,
                "mission_id": m.mission_id,
                "kluster_id": m.kluster_id,
                "from_agent_id": m.from_agent_id,
                "to_agent_id": m.to_agent_id,
                "task_id": m.task_id,
                "channel": m.channel,
                "body_json": json.loads(m.body_json or "{}"),
                "in_reply_to": m.in_reply_to,
                "created_at": m.created_at,
                "read_at": m.read_at,
            }
            for m in msgs
        ]


# ---------------------------------------------------------------------------
# Agent message inbox (for daemon message relay loop)
# ---------------------------------------------------------------------------


@router.get("/agents/{agent_id}/messages")
def get_agent_messages(agent_id: str, since_id: int = 0):
    """Return messages directed at a specific agent (or broadcast) since since_id."""
    with get_session() as session:
        q = (
            select(MeshMessage)
            .where(
                (MeshMessage.to_agent_id == agent_id) | (MeshMessage.to_agent_id == None)
            )
            .where(MeshMessage.id > since_id)
            .order_by(MeshMessage.id)
            .limit(100)
        )
        msgs = session.exec(q).all()
        return [
            {
                "id": m.id,
                "mission_id": m.mission_id,
                "kluster_id": m.kluster_id,
                "from_agent_id": m.from_agent_id,
                "to_agent_id": m.to_agent_id,
                "task_id": m.task_id,
                "channel": m.channel,
                "body_json": json.loads(m.body_json or "{}"),
                "in_reply_to": m.in_reply_to,
                "created_at": m.created_at,
                "read_at": m.read_at,
            }
            for m in msgs
        ]


# ---------------------------------------------------------------------------
# Task progress stream (WebSocket — simple polling fallback for now)
# ---------------------------------------------------------------------------


@router.get("/tasks/{task_id}/progress")
def get_task_progress(task_id: str, since_seq: int = -1):
    """Replay progress events for a task, optionally from a given seq."""
    with get_session() as session:
        q = (
            select(MeshProgressEvent)
            .where(MeshProgressEvent.task_id == task_id)
            .where(MeshProgressEvent.seq > since_seq)
            .order_by(MeshProgressEvent.seq)
        )
        events = session.exec(q).all()
        return [
            {
                "id": e.id,
                "task_id": e.task_id,
                "agent_id": e.agent_id,
                "seq": e.seq,
                "event_type": e.event_type,
                "phase": e.phase,
                "step": e.step,
                "summary": e.summary,
                "payload_json": json.loads(e.payload_json or "{}"),
                "occurred_at": e.occurred_at,
            }
            for e in events
        ]


@router.websocket("/klusters/{kluster_id}/stream")
async def kluster_stream(websocket: WebSocket, kluster_id: str):
    """Live feed of progress events and messages for a kluster.

    Simple implementation: client connects and we poll every second.
    Production-grade LISTEN/NOTIFY upgrade is a follow-on.
    """
    import asyncio

    await websocket.accept()
    last_progress_id = 0
    last_msg_id = 0
    try:
        while True:
            events_out = []
            msgs_out = []
            with get_session() as session:
                # Progress events for any task in this kluster
                tasks_q = session.exec(
                    select(MeshTask.id).where(MeshTask.kluster_id == kluster_id)
                ).all()
                task_ids = list(tasks_q)
                if task_ids:
                    evs = session.exec(
                        select(MeshProgressEvent)
                        .where(MeshProgressEvent.id > last_progress_id)
                        .where(MeshProgressEvent.task_id.in_(task_ids))
                        .order_by(MeshProgressEvent.id)
                        .limit(50)
                    ).all()
                    for e in evs:
                        events_out.append({
                            "type": "progress",
                            "task_id": e.task_id,
                            "agent_id": e.agent_id,
                            "seq": e.seq,
                            "event_type": e.event_type,
                            "phase": e.phase,
                            "step": e.step,
                            "summary": e.summary,
                            "occurred_at": e.occurred_at.isoformat(),
                        })
                        last_progress_id = max(last_progress_id, e.id)

                msgs = session.exec(
                    select(MeshMessage)
                    .where(MeshMessage.kluster_id == kluster_id)
                    .where(MeshMessage.id > last_msg_id)
                    .order_by(MeshMessage.id)
                    .limit(20)
                ).all()
                for m in msgs:
                    msgs_out.append({
                        "type": "message",
                        "id": m.id,
                        "from_agent_id": m.from_agent_id,
                        "to_agent_id": m.to_agent_id,
                        "channel": m.channel,
                        "body_json": json.loads(m.body_json or "{}"),
                        "created_at": m.created_at.isoformat(),
                    })
                    last_msg_id = max(last_msg_id, m.id)

            for item in events_out + msgs_out:
                await websocket.send_json(item)

            await asyncio.sleep(1)
    except WebSocketDisconnect:
        pass


@router.websocket("/missions/{mission_id}/stream")
async def mission_stream(websocket: WebSocket, mission_id: str):
    """Live feed of all progress events and messages across a mission."""
    import asyncio

    await websocket.accept()
    last_progress_id = 0
    last_msg_id = 0
    try:
        while True:
            events_out = []
            msgs_out = []
            with get_session() as session:
                task_ids = session.exec(
                    select(MeshTask.id).where(MeshTask.mission_id == mission_id)
                ).all()
                if task_ids:
                    evs = session.exec(
                        select(MeshProgressEvent)
                        .where(MeshProgressEvent.id > last_progress_id)
                        .where(MeshProgressEvent.task_id.in_(task_ids))
                        .order_by(MeshProgressEvent.id)
                        .limit(50)
                    ).all()
                    for e in evs:
                        events_out.append({
                            "type": "progress",
                            "task_id": e.task_id,
                            "agent_id": e.agent_id,
                            "seq": e.seq,
                            "event_type": e.event_type,
                            "phase": e.phase,
                            "step": e.step,
                            "summary": e.summary,
                            "occurred_at": e.occurred_at.isoformat(),
                        })
                        last_progress_id = max(last_progress_id, e.id)

                msgs = session.exec(
                    select(MeshMessage)
                    .where(MeshMessage.mission_id == mission_id)
                    .where(MeshMessage.id > last_msg_id)
                    .order_by(MeshMessage.id)
                    .limit(20)
                ).all()
                for m in msgs:
                    msgs_out.append({
                        "type": "message",
                        "id": m.id,
                        "from_agent_id": m.from_agent_id,
                        "to_agent_id": m.to_agent_id,
                        "channel": m.channel,
                        "body_json": json.loads(m.body_json or "{}"),
                        "created_at": m.created_at.isoformat(),
                    })
                    last_msg_id = max(last_msg_id, m.id)

            for item in events_out + msgs_out:
                await websocket.send_json(item)

            import asyncio as _aio
            await _aio.sleep(1)
    except WebSocketDisconnect:
        pass
