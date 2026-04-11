"""Budget policy and usage tracking router."""
import uuid
from datetime import datetime
from typing import List, Literal, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlmodel import Session, select

from app.db import engine
from app.models import BudgetPolicy, BudgetWindow
from app.services.authz import actor_subject_from_request
from app.services import budget as budget_svc

router = APIRouter(tags=["budgets"])


# ---------------------------------------------------------------------------
# Request/Response schemas
# ---------------------------------------------------------------------------

class BudgetPolicyCreate(BaseModel):
    scope_type: Literal["tenant", "mission", "kluster", "agent", "runtime", "provider"]
    scope_id: str
    window_type: Literal["day", "week", "month", "rolling_24h"]
    hard_cap_cents: int
    soft_cap_cents: Optional[int] = None
    action_on_breach: Literal["pause", "require_approval", "alert_only"] = "alert_only"


class BudgetPolicyRead(BaseModel):
    id: str
    owner_subject: str
    scope_type: str
    scope_id: str
    window_type: str
    hard_cap_cents: int
    soft_cap_cents: Optional[int]
    action_on_breach: str
    active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class UsageBatchItem(BaseModel):
    runtime_kind: str
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    tool_calls: int = 0
    wall_ms: int = 0
    provider: str = "unknown"
    model: str = "unknown"
    run_id: Optional[str] = None
    mesh_task_id: Optional[str] = None
    mesh_agent_id: Optional[str] = None
    mission_id: Optional[str] = None
    kluster_id: Optional[str] = None
    source: str = "adapter"


class UsageBatchRequest(BaseModel):
    records: List[UsageBatchItem]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("", response_model=BudgetPolicyRead)
def create_budget_policy(payload: BudgetPolicyCreate, request: Request):
    owner = actor_subject_from_request(request)
    now = datetime.utcnow()
    policy = BudgetPolicy(
        id=str(uuid.uuid4()),
        owner_subject=owner,
        scope_type=payload.scope_type,
        scope_id=payload.scope_id,
        window_type=payload.window_type,
        hard_cap_cents=payload.hard_cap_cents,
        soft_cap_cents=payload.soft_cap_cents,
        action_on_breach=payload.action_on_breach,
        active=True,
        created_at=now,
        updated_at=now,
    )
    with Session(engine) as session:
        session.add(policy)
        session.commit()
        session.refresh(policy)
    budget_svc.invalidate_policy_cache(owner)
    return BudgetPolicyRead.from_orm(policy)


@router.get("", response_model=List[BudgetPolicyRead])
def list_budget_policies(request: Request):
    owner = actor_subject_from_request(request)
    with Session(engine) as session:
        policies = list(session.exec(
            select(BudgetPolicy).where(BudgetPolicy.owner_subject == owner, BudgetPolicy.active == True)
        ).all())
    return [BudgetPolicyRead.from_orm(p) for p in policies]


@router.get("/usage")
def get_usage_summary(
    request: Request,
    mission_id: Optional[str] = None,
    since: Optional[datetime] = None,
):
    owner = actor_subject_from_request(request)
    return budget_svc.get_usage_summary(owner, mission_id=mission_id, since=since)


@router.post("/usage/batch")
def record_usage_batch(payload: UsageBatchRequest, request: Request):
    owner = actor_subject_from_request(request)
    records = [r.dict() for r in payload.records]
    budget_svc.record_usage_batch(records, owner_subject=owner)
    return {"recorded": len(records)}


@router.get("/{policy_id}", response_model=BudgetPolicyRead)
def get_budget_policy(policy_id: str, request: Request):
    owner = actor_subject_from_request(request)
    with Session(engine) as session:
        policy = session.get(BudgetPolicy, policy_id)
    if not policy or policy.owner_subject != owner:
        raise HTTPException(status_code=404, detail="Budget policy not found")
    return BudgetPolicyRead.from_orm(policy)


@router.delete("/{policy_id}")
def delete_budget_policy(policy_id: str, request: Request):
    owner = actor_subject_from_request(request)
    with Session(engine) as session:
        policy = session.get(BudgetPolicy, policy_id)
        if not policy or policy.owner_subject != owner:
            raise HTTPException(status_code=404, detail="Budget policy not found")
        policy.active = False
        policy.updated_at = datetime.utcnow()
        session.add(policy)
        session.commit()
    budget_svc.invalidate_policy_cache(owner)
    return {"deleted": policy_id}


@router.post("/{policy_id}/override")
def override_budget_window(policy_id: str, request: Request):
    """Reset latest window state to 'open'. Allowed for owner or service-token."""
    owner = actor_subject_from_request(request)
    with Session(engine) as session:
        policy = session.get(BudgetPolicy, policy_id)
        if not policy:
            raise HTTPException(status_code=404, detail="Budget policy not found")
        # Allow owner or service-token (platform admin)
        if policy.owner_subject != owner and owner != "service-token":
            raise HTTPException(status_code=403, detail="Forbidden")

        window = session.exec(
            select(BudgetWindow)
            .where(BudgetWindow.policy_id == policy_id)
            .order_by(BudgetWindow.window_start.desc())
        ).first()
        if not window:
            raise HTTPException(status_code=404, detail="No active window found")
        window.state = "open"
        window.updated_at = datetime.utcnow()
        session.add(window)
        session.commit()
    return {"reset": policy_id}
