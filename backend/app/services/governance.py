from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from copy import deepcopy
from datetime import datetime
from typing import Any

from fastapi import HTTPException, Request
from sqlmodel import Session, select

from app.models import ApprovalNonceUse, ApprovalRequest, GovernancePolicy, GovernancePolicyEvent
from app.services.authz import actor_subject_from_request, is_platform_admin
from app.services.notifications import emit_controlplane_event

POLICY_STATE_DRAFT = "draft"
POLICY_STATE_ACTIVE = "active"
POLICY_STATE_ARCHIVED = "archived"

VALID_STATES = {POLICY_STATE_DRAFT, POLICY_STATE_ACTIVE, POLICY_STATE_ARCHIVED}

DEFAULT_POLICY = {
    "global": {
        "require_approval_for_mutations": False,
        "allow_create_without_approval": True,
        "allow_update": True,
        "allow_delete": True,
        "allow_publish": True,
    },
    "actions": {
        "mission.create": {"enabled": True, "requires_approval": False},
        "mission.update": {"enabled": True, "requires_approval": False},
        "mission.delete": {"enabled": True, "requires_approval": False},
        "mission.publish": {"enabled": True, "requires_approval": False},
        "kluster.create": {"enabled": True, "requires_approval": False},
        "kluster.update": {"enabled": True, "requires_approval": False},
        "kluster.delete": {"enabled": True, "requires_approval": False},
        "kluster.publish": {"enabled": True, "requires_approval": False},
        "task.create": {"enabled": True, "requires_approval": False},
        "task.update": {"enabled": True, "requires_approval": False},
        "task.delete": {"enabled": True, "requires_approval": False},
        "task.publish": {"enabled": True, "requires_approval": False},
        "doc.create": {"enabled": True, "requires_approval": False},
        "doc.update": {"enabled": True, "requires_approval": False},
        "doc.delete": {"enabled": True, "requires_approval": False},
        "doc.publish": {"enabled": True, "requires_approval": False},
        "artifact.create": {"enabled": True, "requires_approval": False},
        "artifact.update": {"enabled": True, "requires_approval": False},
        "artifact.delete": {"enabled": True, "requires_approval": False},
        "artifact.publish": {"enabled": True, "requires_approval": False},
        "skills.bundle.publish": {"enabled": True, "requires_approval": False},
        "skills.bundle.deprecate": {"enabled": True, "requires_approval": False},
        "skills.snapshot.resolve": {"enabled": True, "requires_approval": False},
        "workspace.load": {"enabled": True, "requires_approval": False},
        "workspace.heartbeat": {"enabled": True, "requires_approval": False},
        "workspace.fetch_artifact": {"enabled": True, "requires_approval": False},
        "workspace.commit": {"enabled": True, "requires_approval": False},
        "workspace.release": {"enabled": True, "requires_approval": False},
    },
    "terminal": {
        "allow_create_actions": True,
        "allow_publish_actions": False,
    },
    "mcp": {
        "allow_mutation_tools": True,
    },
}


def ensure_governance_policy_seed(session: Session) -> GovernancePolicy:
    active = _get_active_policy_row(session)
    if active:
        return active
    row = GovernancePolicy(
        version=1,
        state=POLICY_STATE_ACTIVE,
        policy_json=json.dumps(DEFAULT_POLICY, separators=(",", ":")),
        change_note="Initial default governance policy",
        created_by="system",
        published_by="system",
        published_at=datetime.utcnow(),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    _log_event(
        session=session,
        policy_id=row.id,
        version=row.version,
        event_type="seeded",
        actor_subject="system",
        detail={"note": "seed default policy"},
    )
    session.commit()
    return row


def _get_active_policy_row(session: Session) -> GovernancePolicy | None:
    return session.exec(
        select(GovernancePolicy)
        .where(GovernancePolicy.state == POLICY_STATE_ACTIVE)
        .order_by(GovernancePolicy.version.desc())
    ).first()


def _parse_policy_json(raw: str | None) -> dict[str, Any]:
    if not raw:
        return deepcopy(DEFAULT_POLICY)
    try:
        payload = json.loads(raw)
    except Exception:
        return deepcopy(DEFAULT_POLICY)
    if not isinstance(payload, dict):
        return deepcopy(DEFAULT_POLICY)
    return _merge_policy_defaults(payload)


def _merge_policy_defaults(payload: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(DEFAULT_POLICY)
    for section in ("global", "terminal", "mcp"):
        incoming = payload.get(section)
        if isinstance(incoming, dict):
            merged_section = merged.get(section) or {}
            merged_section.update(incoming)
            merged[section] = merged_section
    incoming_actions = payload.get("actions")
    if isinstance(incoming_actions, dict):
        merged_actions = merged.get("actions") or {}
        for action, spec in incoming_actions.items():
            if not isinstance(spec, dict):
                continue
            baseline = merged_actions.get(action, {"enabled": True, "requires_approval": False})
            baseline.update(spec)
            merged_actions[action] = baseline
        merged["actions"] = merged_actions
    return merged


def get_active_policy(session: Session) -> dict[str, Any]:
    row = _get_active_policy_row(session) or ensure_governance_policy_seed(session)
    return apply_env_overrides(_parse_policy_json(row.policy_json))


def get_active_policy_row(session: Session) -> GovernancePolicy:
    return _get_active_policy_row(session) or ensure_governance_policy_seed(session)


def list_policy_versions(session: Session) -> list[GovernancePolicy]:
    return session.exec(select(GovernancePolicy).order_by(GovernancePolicy.version.desc())).all()


def list_policy_events(session: Session, limit: int = 100) -> list[GovernancePolicyEvent]:
    return session.exec(
        select(GovernancePolicyEvent)
        .order_by(GovernancePolicyEvent.created_at.desc())
        .limit(limit)
    ).all()


def _next_version(session: Session) -> int:
    latest = session.exec(select(GovernancePolicy).order_by(GovernancePolicy.version.desc())).first()
    return (latest.version + 1) if latest else 1


def create_policy_draft(
    *,
    session: Session,
    actor_subject: str,
    policy: dict[str, Any] | None = None,
    note: str = "",
) -> GovernancePolicy:
    merged = policy or deepcopy(DEFAULT_POLICY)
    validate_policy(merged)
    row = GovernancePolicy(
        version=_next_version(session),
        state=POLICY_STATE_DRAFT,
        policy_json=json.dumps(merged, separators=(",", ":")),
        change_note=note,
        created_by=actor_subject,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    _log_event(
        session=session,
        policy_id=row.id,
        version=row.version,
        event_type="draft_created",
        actor_subject=actor_subject,
        detail={"note": note},
    )
    session.commit()
    return row


def update_policy_draft(
    *,
    session: Session,
    draft_id: int,
    actor_subject: str,
    policy: dict[str, Any],
    note: str = "",
) -> GovernancePolicy:
    row = session.get(GovernancePolicy, draft_id)
    if not row:
        raise HTTPException(status_code=404, detail="Governance draft not found")
    if row.state != POLICY_STATE_DRAFT:
        raise HTTPException(status_code=409, detail="Only draft policies can be edited")
    validate_policy(policy)
    row.policy_json = json.dumps(policy, separators=(",", ":"))
    if note:
        row.change_note = note
    row.updated_at = datetime.utcnow()
    session.add(row)
    session.commit()
    session.refresh(row)
    _log_event(
        session=session,
        policy_id=row.id,
        version=row.version,
        event_type="draft_updated",
        actor_subject=actor_subject,
        detail={"note": note},
    )
    session.commit()
    return row


def publish_policy_draft(
    *,
    session: Session,
    draft_id: int,
    actor_subject: str,
    note: str = "",
) -> GovernancePolicy:
    row = session.get(GovernancePolicy, draft_id)
    if not row:
        raise HTTPException(status_code=404, detail="Governance draft not found")
    if row.state != POLICY_STATE_DRAFT:
        raise HTTPException(status_code=409, detail="Only draft policies can be published")
    policy = _parse_policy_json(row.policy_json)
    validate_policy(policy)
    current = _get_active_policy_row(session)
    if current:
        current.state = POLICY_STATE_ARCHIVED
        current.updated_at = datetime.utcnow()
        session.add(current)
    row.state = POLICY_STATE_ACTIVE
    row.published_by = actor_subject
    row.published_at = datetime.utcnow()
    if note:
        row.change_note = note
    row.updated_at = datetime.utcnow()
    session.add(row)
    session.commit()
    session.refresh(row)
    _log_event(
        session=session,
        policy_id=row.id,
        version=row.version,
        event_type="published",
        actor_subject=actor_subject,
        detail={"note": note},
    )
    session.commit()
    return row


def rollback_policy(
    *,
    session: Session,
    version: int,
    actor_subject: str,
    note: str = "",
) -> GovernancePolicy:
    source = session.exec(select(GovernancePolicy).where(GovernancePolicy.version == version)).first()
    if not source:
        raise HTTPException(status_code=404, detail="Governance policy version not found")
    policy = _parse_policy_json(source.policy_json)
    new_row = create_policy_draft(
        session=session,
        actor_subject=actor_subject,
        policy=policy,
        note=note or f"Rollback from version {version}",
    )
    published = publish_policy_draft(
        session=session,
        draft_id=new_row.id,
        actor_subject=actor_subject,
        note=note or f"Rollback from version {version}",
    )
    _log_event(
        session=session,
        policy_id=published.id,
        version=published.version,
        event_type="rollback",
        actor_subject=actor_subject,
        detail={"from_version": version, "note": note},
    )
    session.commit()
    return published


def validate_policy(policy: dict[str, Any]) -> None:
    if not isinstance(policy, dict):
        raise HTTPException(status_code=422, detail="policy must be an object")
    for key in ("global", "actions", "terminal", "mcp"):
        if key not in policy or not isinstance(policy[key], dict):
            raise HTTPException(status_code=422, detail=f"policy.{key} must be an object")

    global_required = {
        "require_approval_for_mutations",
        "allow_create_without_approval",
        "allow_update",
        "allow_delete",
        "allow_publish",
    }
    for key in global_required:
        if key not in policy["global"] or not isinstance(policy["global"][key], bool):
            raise HTTPException(status_code=422, detail=f"policy.global.{key} must be boolean")

    for action, spec in policy["actions"].items():
        if not isinstance(action, str) or "." not in action:
            raise HTTPException(status_code=422, detail=f"invalid action key: {action}")
        if not isinstance(spec, dict):
            raise HTTPException(status_code=422, detail=f"policy.actions.{action} must be object")
        if not isinstance(spec.get("enabled"), bool):
            raise HTTPException(status_code=422, detail=f"policy.actions.{action}.enabled must be boolean")
        if not isinstance(spec.get("requires_approval"), bool):
            raise HTTPException(
                status_code=422, detail=f"policy.actions.{action}.requires_approval must be boolean"
            )

    if not isinstance(policy["terminal"].get("allow_create_actions"), bool):
        raise HTTPException(status_code=422, detail="policy.terminal.allow_create_actions must be boolean")
    if not isinstance(policy["terminal"].get("allow_publish_actions"), bool):
        raise HTTPException(status_code=422, detail="policy.terminal.allow_publish_actions must be boolean")
    if not isinstance(policy["mcp"].get("allow_mutation_tools"), bool):
        raise HTTPException(status_code=422, detail="policy.mcp.allow_mutation_tools must be boolean")


def apply_env_overrides(policy: dict[str, Any]) -> dict[str, Any]:
    out = deepcopy(policy)
    profile = (os.getenv("MC_GOV_PROFILE") or "").strip().lower()
    if profile == "production":
        out.setdefault("global", {})
        out["global"]["require_approval_for_mutations"] = True
        out["global"]["allow_create_without_approval"] = False
        out.setdefault("actions", {})
        for action, spec in list(out["actions"].items()):
            if not isinstance(spec, dict):
                continue
            # Conservative profile: all enabled mutations require explicit approval.
            spec["requires_approval"] = True
            out["actions"][action] = spec
        out.setdefault("mcp", {})
        out["mcp"]["allow_mutation_tools"] = False
        out.setdefault("terminal", {})
        out["terminal"]["allow_create_actions"] = False
        out["terminal"]["allow_publish_actions"] = False

    env_map = {
        "MC_GOV_REQUIRE_APPROVAL_FOR_MUTATIONS": ("global", "require_approval_for_mutations"),
        "MC_GOV_ALLOW_CREATE_WITHOUT_APPROVAL": ("global", "allow_create_without_approval"),
        "MC_GOV_ALLOW_UPDATE": ("global", "allow_update"),
        "MC_GOV_ALLOW_DELETE": ("global", "allow_delete"),
        "MC_GOV_ALLOW_PUBLISH": ("global", "allow_publish"),
        "MC_GOV_MCP_ALLOW_MUTATION_TOOLS": ("mcp", "allow_mutation_tools"),
        "MC_GOV_TERMINAL_ALLOW_CREATE_ACTIONS": ("terminal", "allow_create_actions"),
        "MC_GOV_TERMINAL_ALLOW_PUBLISH_ACTIONS": ("terminal", "allow_publish_actions"),
    }
    for env_key, target in env_map.items():
        raw = os.getenv(env_key)
        if raw is None:
            continue
        parsed = _to_bool(raw)
        if parsed is None:
            continue
        section, name = target
        out.setdefault(section, {})
        out[section][name] = parsed
    return out


def _to_bool(raw: str) -> bool | None:
    lowered = raw.strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    return None


def _approval_token_secret() -> str:
    return (os.getenv("MC_APPROVAL_TOKEN_SECRET") or "").strip()


def _legacy_approval_context_allowed() -> bool:
    raw = os.getenv("MC_ALLOW_LEGACY_APPROVAL_CONTEXT")
    if raw is None:
        # Default to strict mode when token secret is configured.
        return not bool(_approval_token_secret())
    parsed = _to_bool(raw)
    return bool(parsed)


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    pad = "=" * ((4 - len(value) % 4) % 4)
    return base64.urlsafe_b64decode((value + pad).encode("utf-8"))


def generate_approval_token(payload: dict[str, Any], *, secret: str | None = None) -> str:
    token_secret = (secret or _approval_token_secret()).strip()
    if not token_secret:
        raise ValueError("Approval token secret is required")
    payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    sig = hmac.new(token_secret.encode("utf-8"), payload_b64.encode("utf-8"), hashlib.sha256).digest()
    return f"{payload_b64}.{_b64url_encode(sig)}"


def parse_approval_token(raw_token: str | None, *, secret: str | None = None) -> dict[str, Any] | None:
    if not raw_token or not isinstance(raw_token, str):
        return None
    token = raw_token.strip()
    if not token:
        return None
    token_secret = (secret or _approval_token_secret()).strip()
    if not token_secret:
        return None
    if "." not in token:
        return None
    payload_b64, sig_b64 = token.split(".", 1)
    if not payload_b64 or not sig_b64:
        return None
    expected_sig = hmac.new(token_secret.encode("utf-8"), payload_b64.encode("utf-8"), hashlib.sha256).digest()
    try:
        supplied_sig = _b64url_decode(sig_b64)
    except Exception:
        return None
    if not hmac.compare_digest(expected_sig, supplied_sig):
        return None
    try:
        payload = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    exp = payload.get("exp")
    if not isinstance(exp, int):
        return None
    if exp <= int(datetime.utcnow().timestamp()):
        return None
    required = ("request_id", "approved_by", "approved_at")
    for key in required:
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip():
            return None
    out = dict(payload)
    out["token_verified"] = True
    return out


def require_policy_action(
    *,
    session: Session,
    action: str,
    request: Request,
    approval_context: dict[str, Any] | None = None,
    channel: str = "api",
) -> dict[str, Any]:
    policy = get_active_policy(session)
    decision = evaluate_action(policy=policy, action=action, approval_context=approval_context, channel=channel)
    if decision["allowed"] and decision.get("reason") == "ok_with_approval":
        replay = _consume_verified_approval_nonce(
            session=session,
            action=action,
            request=request,
            approval_context=approval_context,
        )
        if replay:
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "governance_denied",
                    "action": action,
                    "reason": replay,
                },
            )
        trace = approval_trace_from_context(approval_context)
        if trace and request is not None:
            try:
                setattr(request.state, "approval_trace", trace)
            except Exception:
                pass
    if not decision["allowed"]:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "governance_denied",
                "action": action,
                "reason": decision["reason"],
            },
        )
    return decision


def approval_trace_from_context(approval_context: dict[str, Any] | None) -> dict[str, str] | None:
    if not isinstance(approval_context, dict):
        return None
    out: dict[str, str] = {}
    approval_request_id = approval_context.get("approval_request_id")
    if isinstance(approval_request_id, int):
        out["approval_request_id"] = str(approval_request_id)
    nonce = approval_context.get("nonce")
    if isinstance(nonce, str) and nonce.strip():
        out["approval_nonce"] = nonce.strip()
    return out or None


def _consume_verified_approval_nonce(
    *,
    session: Session,
    action: str,
    request: Request | None,
    approval_context: dict[str, Any] | None,
) -> str | None:
    if not isinstance(approval_context, dict) or not approval_context.get("token_verified"):
        return None
    nonce = approval_context.get("nonce")
    if not isinstance(nonce, str) or not nonce.strip():
        return "approval_nonce_missing"
    nonce = nonce.strip()
    existing = session.exec(select(ApprovalNonceUse).where(ApprovalNonceUse.nonce == nonce)).first()
    if existing:
        return "approval_token_replay"

    request_id = ""
    if request is not None:
        request_id = (getattr(request, "headers", {}) or {}).get("x-request-id") or ""

    nonce_use = ApprovalNonceUse(
        nonce=nonce,
        request_id=request_id,
        action=action,
        actor_subject=actor_subject_from_request(request),
    )
    approval_request_id = approval_context.get("approval_request_id")
    if isinstance(approval_request_id, int):
        nonce_use.approval_request_id = approval_request_id
        approval_row = session.get(ApprovalRequest, approval_request_id)
        if approval_row is not None:
            if approval_row.status not in {"approved", "executed"}:
                return "approval_request_not_approved"
            if approval_row.approval_nonce and approval_row.approval_nonce != nonce:
                return "approval_nonce_mismatch"
            if approval_row.action and approval_row.action != action:
                return "approval_action_mismatch"
            if approval_row.status == "approved":
                approval_row.status = "executed"
                approval_row.executed_at = datetime.utcnow()
                approval_row.executed_action = action
                approval_row.executed_request_id = request_id
                approval_row.updated_at = datetime.utcnow()
                session.add(approval_row)
                emit_controlplane_event(
                    req=request,
                    event_type="approval.executed",
                    payload={
                        "approval_request_id": approval_row.id,
                        "mission_id": approval_row.mission_id,
                        "action": action,
                        "request_id": request_id,
                        "actor_subject": actor_subject_from_request(request),
                    },
                )
    session.add(nonce_use)
    session.commit()
    return None


def evaluate_action(
    *,
    policy: dict[str, Any],
    action: str,
    approval_context: dict[str, Any] | None = None,
    channel: str = "api",
) -> dict[str, Any]:
    global_cfg = policy.get("global") or {}
    actions_cfg = policy.get("actions") or {}
    action_cfg = actions_cfg.get(action) or {"enabled": False, "requires_approval": True}

    if channel == "mcp" and not (policy.get("mcp") or {}).get("allow_mutation_tools", True):
        return {"allowed": False, "reason": "mcp_mutation_tools_disabled"}

    if channel == "terminal":
        terminal_cfg = policy.get("terminal") or {}
        if action.endswith(".create") and not terminal_cfg.get("allow_create_actions", True):
            return {"allowed": False, "reason": "terminal_create_disabled"}
        if action.endswith(".publish") and not terminal_cfg.get("allow_publish_actions", False):
            return {"allowed": False, "reason": "terminal_publish_disabled"}

    if not action_cfg.get("enabled", False):
        return {"allowed": False, "reason": "action_disabled"}

    if action.endswith(".update") and not global_cfg.get("allow_update", False):
        return {"allowed": False, "reason": "global_update_disabled"}
    if action.endswith(".delete") and not global_cfg.get("allow_delete", False):
        return {"allowed": False, "reason": "global_delete_disabled"}
    if action.endswith(".publish") and not global_cfg.get("allow_publish", False):
        return {"allowed": False, "reason": "global_publish_disabled"}

    requires_approval = bool(action_cfg.get("requires_approval", True))
    if not requires_approval:
        return {"allowed": True, "reason": "ok"}

    if action.endswith(".create") and global_cfg.get("allow_create_without_approval", False):
        return {"allowed": True, "reason": "ok_create_no_approval"}

    if not global_cfg.get("require_approval_for_mutations", True):
        return {"allowed": True, "reason": "ok_global_approval_disabled"}

    if not _approval_context_valid(approval_context):
        return {"allowed": False, "reason": "approval_required"}
    return {"allowed": True, "reason": "ok_with_approval"}


def _approval_context_valid(approval_context: dict[str, Any] | None) -> bool:
    if not isinstance(approval_context, dict):
        return False
    for key in ("request_id", "approved_by", "approved_at"):
        value = approval_context.get(key)
        if not isinstance(value, str) or not value.strip():
            return False
    return True


def extract_approval_context(
    request: Request | None,
    *,
    approval_token: str | None = None,
    approval_context: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if request is None:
        context = approval_context if isinstance(approval_context, dict) else None
        token_payload = parse_approval_token(approval_token)
        if token_payload is not None:
            return token_payload
        if _legacy_approval_context_allowed() and context is not None:
            return context
        return None
    headers = getattr(request, "headers", None) or {}
    token_payload = parse_approval_token(approval_token or headers.get("x-approval-token"))
    if token_payload is not None:
        return token_payload
    if not _legacy_approval_context_allowed():
        return None
    context_candidate = approval_context
    if context_candidate is None:
        raw = headers.get("x-approval-context")
        if raw:
            try:
                payload = json.loads(raw)
            except Exception:
                payload = None
            if isinstance(payload, dict):
                context_candidate = payload
    if isinstance(context_candidate, dict):
        return context_candidate
    return None


def ensure_platform_admin(request: Request | None) -> None:
    if not is_platform_admin(request):
        raise HTTPException(status_code=403, detail="Forbidden: platform admin required")


def _log_event(
    *,
    session: Session,
    policy_id: int | None,
    version: int,
    event_type: str,
    actor_subject: str,
    detail: dict[str, Any],
) -> None:
    event = GovernancePolicyEvent(
        policy_id=policy_id,
        version=version,
        event_type=event_type,
        actor_subject=actor_subject,
        detail_json=json.dumps(detail, separators=(",", ":")),
    )
    session.add(event)


def serialize_policy_row(row: GovernancePolicy, *, effective: bool = False, session: Session | None = None) -> dict[str, Any]:
    policy = _parse_policy_json(row.policy_json)
    if effective:
        policy = apply_env_overrides(policy)
    return {
        "id": row.id,
        "version": row.version,
        "state": row.state,
        "policy": policy,
        "change_note": row.change_note,
        "created_by": row.created_by,
        "published_by": row.published_by,
        "published_at": row.published_at,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def serialize_event_row(row: GovernancePolicyEvent) -> dict[str, Any]:
    detail = {}
    try:
        detail = json.loads(row.detail_json or "{}")
    except Exception:
        detail = {}
    return {
        "id": row.id,
        "policy_id": row.policy_id,
        "version": row.version,
        "event_type": row.event_type,
        "actor_subject": row.actor_subject,
        "detail": detail,
        "created_at": row.created_at,
    }
