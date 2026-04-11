"""
Budget enforcement service.
Efficiency principle: zero-budget happy path is O(1) — no DB call, immediate 'allow'.
Policy cache with 5-minute TTL; invalidation via NOTIFY wired in Phase 5.
"""
import time
import uuid
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Literal, Dict

from sqlmodel import Session, select
from app.db import engine
from app.models import BudgetPolicy, BudgetWindow, UsageRecord, CostProfile

logger = logging.getLogger(__name__)

# In-process policy cache: owner_subject -> (policies, loaded_at)
_policy_cache: Dict[str, tuple] = {}
POLICY_CACHE_TTL_SECONDS = 300  # 5 minutes


def _load_policies(owner_subject: str) -> List[BudgetPolicy]:
    """Load active budget policies for this owner. Uses TTL cache."""
    now = time.monotonic()
    cached = _policy_cache.get(owner_subject)
    if cached and (now - cached[1]) < POLICY_CACHE_TTL_SECONDS:
        return cached[0]

    with Session(engine) as session:
        policies = list(session.exec(
            select(BudgetPolicy)
            .where(BudgetPolicy.owner_subject == owner_subject, BudgetPolicy.active == True)
        ).all())

    _policy_cache[owner_subject] = (policies, now)
    return policies


def invalidate_policy_cache(owner_subject: str):
    """Called by NOTIFY handler (Phase 5) when policies change."""
    _policy_cache.pop(owner_subject, None)


def preflight(
    owner_subject: str,
    scope_type: str,
    scope_id: str,
    estimated_cost_cents: int = 0,
) -> Literal["allow", "soft_require_approval", "hard_deny"]:
    """
    O(1) happy path: no policies = immediate 'allow'.
    With policies: check current budget_window against caps.
    """
    policies = _load_policies(owner_subject)
    if not policies:
        return "allow"

    # Find applicable policies for this scope
    applicable = [
        p for p in policies
        if p.scope_type == scope_type and p.scope_id == scope_id
    ]
    if not applicable:
        return "allow"

    # Check each applicable policy's current window
    with Session(engine) as session:
        for policy in applicable:
            window = _get_or_create_window(session, policy)
            projected = window.consumed_cents + estimated_cost_cents
            if policy.hard_cap_cents and projected >= policy.hard_cap_cents:
                return "hard_deny"
            if policy.soft_cap_cents and projected >= policy.soft_cap_cents:
                if policy.action_on_breach == "pause":
                    return "hard_deny"
                elif policy.action_on_breach == "require_approval":
                    return "soft_require_approval"

    return "allow"


def record_usage(
    owner_subject: str,
    runtime_kind: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    reasoning_tokens: int = 0,
    tool_calls: int = 0,
    wall_ms: int = 0,
    provider: str = "unknown",
    model: str = "unknown",
    run_id: Optional[str] = None,
    mesh_task_id: Optional[str] = None,
    mesh_agent_id: Optional[str] = None,
    mission_id: Optional[str] = None,
    kluster_id: Optional[str] = None,
    source: str = "adapter",
) -> UsageRecord:
    """Record a usage event. Computes cost from cost_profile. Updates budget windows."""
    cost_cents = _compute_cost(runtime_kind, provider, model, input_tokens, output_tokens, reasoning_tokens, tool_calls)

    with Session(engine) as session:
        record = UsageRecord(
            id=str(uuid.uuid4()),
            owner_subject=owner_subject,
            runtime_kind=runtime_kind,
            provider=provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens,
            tool_calls=tool_calls,
            wall_ms=wall_ms,
            cost_cents=cost_cents,
            recorded_at=datetime.utcnow(),
            source=source,
            run_id=run_id,
            mesh_task_id=mesh_task_id,
            mesh_agent_id=mesh_agent_id,
            mission_id=mission_id,
            kluster_id=kluster_id,
        )
        session.add(record)

        # Update applicable budget windows
        if cost_cents > 0:
            policies = _load_policies(owner_subject)
            for policy in policies:
                if _scope_matches(policy, mission_id, kluster_id, mesh_agent_id, runtime_kind, provider):
                    window = _get_or_create_window(session, policy)
                    window.consumed_cents += cost_cents
                    _evaluate_window(session, window, policy)
                    session.add(window)

        session.commit()
        session.refresh(record)
        return record


def record_usage_batch(records: list, owner_subject: str):
    """Bulk record usage — used by daemon flush every 5s."""
    for r in records:
        record_usage(owner_subject=owner_subject, **r)


def get_usage_summary(
    owner_subject: str,
    mission_id: Optional[str] = None,
    since: Optional[datetime] = None,
) -> dict:
    """Aggregate usage summary for the owner."""
    with Session(engine) as session:
        q = select(UsageRecord).where(UsageRecord.owner_subject == owner_subject)
        if mission_id:
            q = q.where(UsageRecord.mission_id == mission_id)
        if since:
            q = q.where(UsageRecord.recorded_at >= since)
        records = list(session.exec(q).all())

        total_cost = sum(r.cost_cents for r in records)
        total_input = sum(r.input_tokens for r in records)
        total_output = sum(r.output_tokens for r in records)

        return {
            "total_cost_cents": total_cost,
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "record_count": len(records),
        }


def _compute_cost(runtime_kind: str, provider: str, model: str, input_tokens: int, output_tokens: int, reasoning_tokens: int, tool_calls: int) -> int:
    """Compute cost in cents from cost_profile. Returns 0 if no profile found."""
    with Session(engine) as session:
        profile = session.exec(
            select(CostProfile)
            .where(CostProfile.runtime_kind == runtime_kind, CostProfile.provider == provider, CostProfile.model == model)
        ).first()
        if not profile:
            profile = session.exec(
                select(CostProfile).where(CostProfile.runtime_kind == "default")
            ).first()
        if not profile:
            return 0

        input_cost = (input_tokens * profile.input_rate_per_mtok_cents) // 1_000_000
        output_cost = (output_tokens * profile.output_rate_per_mtok_cents) // 1_000_000
        reasoning_cost = (reasoning_tokens * profile.reasoning_rate_per_mtok_cents) // 1_000_000
        tool_cost = tool_calls * profile.tool_call_flat_cents
        return input_cost + output_cost + reasoning_cost + tool_cost


def _get_or_create_window(session: Session, policy: BudgetPolicy) -> BudgetWindow:
    """Get or create the current budget window for a policy."""
    now = datetime.utcnow()
    window = session.exec(
        select(BudgetWindow)
        .where(BudgetWindow.policy_id == policy.id, BudgetWindow.state.in_(["open", "soft_tripped"]))
        .order_by(BudgetWindow.window_start.desc())
    ).first()

    if window and window.window_end > now:
        return window

    # Create new window
    start, end = _window_bounds(policy.window_type, now)
    window = BudgetWindow(
        id=str(uuid.uuid4()),
        policy_id=policy.id,
        window_start=start,
        window_end=end,
        consumed_cents=0,
        state="open",
        created_at=now,
        updated_at=now,
    )
    session.add(window)
    return window


def _window_bounds(window_type: str, now: datetime):
    if window_type == "day":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
    elif window_type == "week":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=now.weekday())
        end = start + timedelta(weeks=1)
    elif window_type == "month":
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        import calendar
        days = calendar.monthrange(now.year, now.month)[1]
        end = start + timedelta(days=days)
    else:  # rolling_24h — window lasts 24h from creation, then resets to 0
        start = now
        end = now + timedelta(hours=24)
    return start, end


def _scope_matches(policy: BudgetPolicy, mission_id, kluster_id, agent_id, runtime_kind, provider) -> bool:
    """Check if this usage event applies to this policy's scope."""
    if policy.scope_type == "mission" and mission_id:
        return policy.scope_id == mission_id
    if policy.scope_type == "kluster" and kluster_id:
        return policy.scope_id == kluster_id
    if policy.scope_type == "agent" and agent_id:
        return policy.scope_id == agent_id
    if policy.scope_type == "runtime":
        return policy.scope_id == runtime_kind
    if policy.scope_type == "provider":
        return policy.scope_id == provider
    if policy.scope_type == "tenant":
        return True  # applies to all usage for this owner
    return False


def _evaluate_window(session: Session, window: BudgetWindow, policy: BudgetPolicy):
    """Flip window state if caps are breached. Emit NOTIFY in Phase 5."""
    if policy.hard_cap_cents and window.consumed_cents >= policy.hard_cap_cents:
        window.state = "hard_tripped"
    elif policy.soft_cap_cents and window.consumed_cents >= policy.soft_cap_cents:
        window.state = "soft_tripped"
    window.updated_at = datetime.utcnow()
