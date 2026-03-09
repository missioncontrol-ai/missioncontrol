import json
import secrets
import shlex
import hashlib
from datetime import datetime, timedelta
from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from app.db import get_session
from app.models import (
    ApprovalRequest,
    ChatInboundReceipt,
    Kluster,
    Mission,
    MissionRoleMembership,
    OverlapSuggestion,
    SlackChannelBinding,
    Task,
)
from app.schemas import SlackChannelBindingCreate, SlackChannelBindingRead
from app.services.authz import (
    actor_subject_from_request,
    assert_mission_owner_or_admin,
    assert_mission_reader_or_admin,
)
from app.services.governance import (
    extract_approval_context,
    generate_approval_token,
    require_policy_action,
)
from app.services.chat_providers import get_slack_provider
from app.services.chat_providers import get_chat_provider
from app.services.notifications import emit_controlplane_event
from app.services.overlap import score_overlap, score_overlap_vector, task_text
from app.services.slack import command_mission_id
from app.services.vectorstore import index_task
from app.services.task_identity import ensure_task_public_id

router = APIRouter(prefix="/integrations/slack", tags=["slack"])
slack_provider = get_slack_provider()


def _serialize_binding(row: SlackChannelBinding) -> dict:
    metadata = {}
    if row.channel_metadata_json:
        try:
            parsed = json.loads(row.channel_metadata_json)
            if isinstance(parsed, dict):
                metadata = parsed
        except Exception:
            metadata = {}
    return {
        "id": row.id,
        "provider": row.provider or "slack",
        "mission_id": row.mission_id,
        "workspace_external_id": row.workspace_external_id or "",
        "channel_id": row.channel_id,
        "channel_name": row.channel_name,
        "channel_metadata": metadata,
        "created_by": row.created_by,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _binding_exists(*, provider: str, mission_id: str, channel_id: str) -> bool:
    with get_session() as session:
        row = session.exec(
            select(SlackChannelBinding)
            .where(SlackChannelBinding.provider == provider)
            .where(SlackChannelBinding.mission_id == mission_id)
            .where(SlackChannelBinding.channel_id == channel_id)
        ).first()
        return row is not None


def _slack_subject(user_id: str) -> str:
    return f"slack:{(user_id or '').strip()}"


def _subject_has_role(*, session, mission_id: str, subject: str, roles: set[str]) -> bool:
    if not subject:
        return False
    row = session.exec(
        select(MissionRoleMembership)
        .where(MissionRoleMembership.mission_id == mission_id)
        .where(MissionRoleMembership.subject == subject)
    ).first()
    return bool(row and row.role in roles)


def _subject_can_write(*, session, mission_id: str, subject: str) -> bool:
    return _subject_has_role(
        session=session,
        mission_id=mission_id,
        subject=subject,
        roles={"mission_owner", "mission_contributor"},
    )


def _subject_can_approve(*, session, mission_id: str, subject: str) -> bool:
    return _subject_has_role(
        session=session,
        mission_id=mission_id,
        subject=subject,
        roles={"mission_owner"},
    )


def _parse_command_kv(text: str) -> tuple[str, dict[str, str]]:
    parts = shlex.split(text or "")
    command = parts[0] if parts else ""
    kv: dict[str, str] = {}
    for token in parts[1:]:
        if "=" not in token:
            continue
        k, v = token.split("=", 1)
        kv[k.strip()] = v.strip()
    return command, kv


def _command_response(msg: str, *, blocks: list[dict] | None = None, in_channel: bool = False) -> dict:
    return slack_provider.response(text=msg, blocks=blocks, in_channel=in_channel)


def _normalize_provider(provider: str | None) -> str:
    name = (provider or "slack").strip().lower() or "slack"
    if get_chat_provider(name) is None:
        raise HTTPException(status_code=400, detail=f"Unsupported chat provider: {name}")
    return name


def _inbound_event_key(*, event_type: str, raw: bytes, mission_id: str = "", channel_id: str = "") -> str:
    raw_hash = hashlib.sha256(raw).hexdigest()
    return f"slack:{event_type}:{mission_id}:{channel_id}:{raw_hash}"


def _record_receipt(*, session, event_key: str, event_type: str) -> bool:
    row = ChatInboundReceipt(provider="slack", event_key=event_key, event_type=event_type)
    session.add(row)
    try:
        session.commit()
        return True
    except IntegrityError:
        session.rollback()
        return False


@router.post("/bindings", response_model=SlackChannelBindingRead)
def create_binding(payload: SlackChannelBindingCreate, request: Request):
    provider = _normalize_provider(payload.provider)
    with get_session() as session:
        assert_mission_owner_or_admin(session=session, request=request, mission_id=payload.mission_id)
        existing = session.exec(
            select(SlackChannelBinding)
            .where(SlackChannelBinding.provider == provider)
            .where(SlackChannelBinding.mission_id == payload.mission_id)
            .where(SlackChannelBinding.channel_id == payload.channel_id)
        ).first()
        if existing:
            return _serialize_binding(existing)
        row = SlackChannelBinding(
            provider=provider,
            mission_id=payload.mission_id,
            workspace_external_id=payload.workspace_external_id or "",
            channel_id=payload.channel_id,
            channel_name=payload.channel_name or "",
            channel_metadata_json=json.dumps(payload.channel_metadata or {}, separators=(",", ":")),
            created_by=actor_subject_from_request(request),
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return _serialize_binding(row)


@router.get("/bindings", response_model=list[SlackChannelBindingRead])
def list_bindings(mission_id: str, request: Request, provider: str = "slack"):
    provider_name = _normalize_provider(provider)
    with get_session() as session:
        assert_mission_reader_or_admin(session=session, request=request, mission_id=mission_id)
        rows = session.exec(
            select(SlackChannelBinding)
            .where(SlackChannelBinding.provider == provider_name)
            .where(SlackChannelBinding.mission_id == mission_id)
            .order_by(SlackChannelBinding.updated_at.desc())
        ).all()
        return [_serialize_binding(row) for row in rows]


@router.delete("/bindings/{binding_id}")
def delete_binding(binding_id: int, request: Request):
    with get_session() as session:
        row = session.get(SlackChannelBinding, binding_id)
        if not row:
            raise HTTPException(status_code=404, detail="Slack binding not found")
        assert_mission_owner_or_admin(session=session, request=request, mission_id=row.mission_id)
        session.delete(row)
        session.commit()
        return {"ok": True, "deleted_id": binding_id}


@router.post("/events")
async def slack_events(request: Request):
    raw = await request.body()
    valid, reason = slack_provider.verify(headers=request.headers, body=raw)
    if not valid:
        raise HTTPException(status_code=401, detail={"code": "slack_signature_invalid", "reason": reason})
    try:
        payload = json.loads(raw.decode("utf-8") or "{}")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid Slack payload")

    if payload.get("type") == "url_verification":
        return {"challenge": payload.get("challenge", "")}

    event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
    channel_id = (
        event.get("channel")
        or (event.get("item") or {}).get("channel")
        or payload.get("channel_id")
        or ""
    )
    mission_id = str(payload.get("mission_id") or "").strip()
    if mission_id and channel_id and not _binding_exists(provider="slack", mission_id=mission_id, channel_id=channel_id):
        raise HTTPException(status_code=403, detail="Slack channel is not bound to mission")
    with get_session() as session:
        event_key = _inbound_event_key(
            event_type=str(payload.get("type") or "event"),
            mission_id=mission_id,
            channel_id=channel_id,
            raw=raw,
        )
        if not _record_receipt(session=session, event_key=event_key, event_type="event"):
            return {"ok": True, "duplicate": True}

    emit_controlplane_event(
        req=request,
        event_type="slack.event.received",
        payload={"event_type": payload.get("type", ""), "channel_id": channel_id, "mission_id": mission_id},
    )
    return {"ok": True}


@router.post("/commands")
async def slack_commands(request: Request):
    raw = await request.body()
    valid, reason = slack_provider.verify(headers=request.headers, body=raw)
    if not valid:
        raise HTTPException(status_code=401, detail={"code": "slack_signature_invalid", "reason": reason})
    form = parse_qs(raw.decode("utf-8"))
    command = (form.get("command") or [""])[0]
    text = (form.get("text") or [""])[0]
    channel_id = (form.get("channel_id") or [""])[0]
    user_id = (form.get("user_id") or [""])[0]
    slack_subject = _slack_subject(user_id)
    command_key, kv = _parse_command_kv(text)
    mission_id = command_mission_id(text, kv.get("mission_id") or (form.get("mission_id") or [""])[0])

    if mission_id and channel_id and not _binding_exists(provider="slack", mission_id=mission_id, channel_id=channel_id):
        return _command_response(f"Channel `{channel_id}` is not bound to mission `{mission_id}`.")
    with get_session() as session:
        event_key = _inbound_event_key(event_type="command", mission_id=mission_id or "", channel_id=channel_id, raw=raw)
        if not _record_receipt(session=session, event_key=event_key, event_type="command"):
            return _command_response("Duplicate command received. Ignoring replay.")

    with get_session() as session:
        if mission_id:
            mission = session.get(Mission, mission_id)
            if not mission:
                return _command_response(f"Mission `{mission_id}` not found.")

        if command_key == "task.create":
            kluster_id = kv.get("kluster_id", "")
            title = kv.get("title", "")
            description = kv.get("description", "")
            approval_token = kv.get("approval_token")
            if not mission_id or not kluster_id or not title:
                return _command_response(
                    "Usage: task.create mission_id=<id> kluster_id=<id> title=<text> [description=<text>]"
                )
            if not _subject_can_write(session=session, mission_id=mission_id, subject=slack_subject):
                return _command_response("Forbidden: Slack user is not a mission contributor/owner.")
            approval_context = extract_approval_context(None, approval_token=approval_token)
            fake_request = type(
                "SlackRequest",
                (),
                {
                    "headers": {"x-request-id": f"slack-cmd-{int(datetime.utcnow().timestamp())}"},
                    "state": type("State", (), {"principal": {"subject": slack_subject, "email": slack_subject}})(),
                    "app": request.app,
                },
            )()
            try:
                require_policy_action(
                    session=session,
                    action="task.create",
                    request=fake_request,
                    approval_context=approval_context,
                    channel="api",
                )
            except HTTPException as exc:
                detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
                return _command_response(f"Policy denied task.create: {detail}")
            kluster = session.get(Kluster, kluster_id)
            if not kluster or kluster.mission_id != mission_id:
                return _command_response("Kluster not found in mission.")
            task = Task(kluster_id=kluster_id, title=title, description=description, owner=slack_subject)
            session.add(task)
            session.commit()
            session.refresh(task)
            ensure_task_public_id(session, task)

            index_task(task.id, task_text(task), {"kluster_id": task.kluster_id, "status": task.status})
            candidates = session.exec(select(Task).where(Task.id != task.id).where(Task.kluster_id == kluster_id)).all()
            overlaps = score_overlap(task, candidates)
            created_overlap_count = 0
            for cand, score, evidence in overlaps[:5]:
                session.add(
                    OverlapSuggestion(
                        task_id=task.id,
                        candidate_task_id=cand.id,
                        similarity_score=score,
                        evidence=evidence,
                        suggested_action="link",
                    )
                )
                created_overlap_count += 1
            vector_matches = score_overlap_vector(task)
            for cand_id, dist in vector_matches:
                session.add(
                    OverlapSuggestion(
                        task_id=task.id,
                        candidate_task_id=cand_id,
                        similarity_score=float(100.0 - dist),
                        evidence=f"Vector similarity distance {dist:.3f}",
                        suggested_action="link",
                    )
                )
                created_overlap_count += 1
            session.commit()
            emit_controlplane_event(
                req=request,
                event_type="slack.task.created",
                payload={
                    "task_id": task.public_id or str(task.id),
                    "mission_id": mission_id,
                    "kluster_id": kluster_id,
                    "overlaps": created_overlap_count,
                },
            )
            blocks = slack_provider.task_created_blocks(
                task_id=task.public_id or str(task.id),
                kluster_id=kluster_id,
                overlaps=created_overlap_count,
            )
            return _command_response(
                f"Created task `{task.public_id or task.id}` in `{kluster_id}`. Overlap suggestions: {created_overlap_count}.",
                blocks=blocks,
            )

        if command_key == "approval.request":
            action = kv.get("action", "")
            reason_text = kv.get("reason", "")
            if not mission_id or not action:
                return _command_response("Usage: approval.request mission_id=<id> action=<entity.action> [reason=<text>]")
            if not _subject_can_write(session=session, mission_id=mission_id, subject=slack_subject):
                return _command_response("Forbidden: Slack user is not a mission contributor/owner.")
            row = ApprovalRequest(
                mission_id=mission_id,
                action=action,
                channel="slack",
                reason=reason_text,
                request_context_json=json.dumps({"channel_id": channel_id, "command": command}),
                status="pending",
                requested_by=slack_subject,
                approval_expires_at=datetime.utcnow(),
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            emit_controlplane_event(
                req=request,
                event_type="approval.requested",
                payload={"approval_request_id": row.id, "mission_id": mission_id, "action": action, "requested_by": slack_subject, "channel": "slack"},
            )
            blocks = slack_provider.approval_request_blocks(
                approval_id=row.id or 0,
                mission_id=mission_id,
                action=action,
                reason=reason_text,
            )
            return _command_response(
                f"Created approval request #{row.id} for `{action}`.",
                blocks=blocks,
                in_channel=True,
            )

        if command_key == "search":
            query = kv.get("q") or kv.get("query") or ""
            if not mission_id or not query:
                return _command_response("Usage: search mission_id=<id> q=<text>")
            if not _subject_has_role(
                session=session,
                mission_id=mission_id,
                subject=slack_subject,
                roles={"mission_owner", "mission_contributor", "mission_viewer"},
            ):
                return _command_response("Forbidden: Slack user is not a mission member.")
            klusters = session.exec(select(Kluster).where(Kluster.mission_id == mission_id)).all()
            kluster_ids = {k.id for k in klusters}
            tasks = session.exec(
                select(Task)
                .where(Task.kluster_id.in_(kluster_ids))
                .where(Task.title.ilike(f"%{query}%"))
                .limit(5)
            ).all() if kluster_ids else []
            if not tasks:
                return _command_response("No matching tasks found.")
            summary = ", ".join([f"#{t.id}:{t.title}" for t in tasks[:5]])
            blocks = slack_provider.search_blocks(summary_rows=[f"#{t.id}: {t.title}" for t in tasks[:5]])
            return _command_response(f"Search results: {summary}", blocks=blocks)

    emit_controlplane_event(
        req=request,
        event_type="slack.command.received",
        payload={
            "command": command,
            "text": text,
            "channel_id": channel_id,
            "user_id": user_id,
            "mission_id": mission_id or "",
        },
    )
    return _command_response("Unknown command. Supported: task.create, approval.request, search")


@router.post("/interactions")
async def slack_interactions(request: Request):
    raw = await request.body()
    valid, reason = slack_provider.verify(headers=request.headers, body=raw)
    if not valid:
        raise HTTPException(status_code=401, detail={"code": "slack_signature_invalid", "reason": reason})
    form = parse_qs(raw.decode("utf-8"))
    raw_payload = (form.get("payload") or ["{}"])[0]
    try:
        payload = json.loads(raw_payload)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid Slack interaction payload")

    channel_id = ((payload.get("channel") or {}) if isinstance(payload, dict) else {}).get("id", "")
    mission_id = str((payload or {}).get("mission_id") or "").strip()
    if mission_id and channel_id and not _binding_exists(provider="slack", mission_id=mission_id, channel_id=channel_id):
        raise HTTPException(status_code=403, detail="Slack channel is not bound to mission")
    with get_session() as session:
        event_key = _inbound_event_key(
            event_type=str((payload or {}).get("type") or "interaction"),
            mission_id=mission_id,
            channel_id=channel_id,
            raw=raw,
        )
        if not _record_receipt(session=session, event_key=event_key, event_type="interaction"):
            return {"ok": True, "duplicate": True}

    actions = payload.get("actions") if isinstance(payload, dict) else None
    user_id = ((payload.get("user") or {}) if isinstance(payload, dict) else {}).get("id", "")
    slack_subject = _slack_subject(user_id)
    if isinstance(actions, list) and actions:
        action = actions[0] if isinstance(actions[0], dict) else {}
        action_id = str(action.get("action_id") or "")
        value = str(action.get("value") or "")
        if action_id in {"mc_approve", "mc_reject"} and value:
            try:
                approval_id = int(value)
            except ValueError:
                return {"ok": False, "error": "invalid approval id"}
            with get_session() as session:
                row = session.get(ApprovalRequest, approval_id)
                if not row:
                    return {"ok": False, "error": "approval request not found"}
                if mission_id and row.mission_id != mission_id:
                    return {"ok": False, "error": "approval mission mismatch"}
                if not _subject_can_approve(session=session, mission_id=row.mission_id, subject=slack_subject):
                    return {"ok": False, "error": "forbidden"}
                if row.status != "pending":
                    return {"ok": False, "error": "approval request is not pending"}
                now = datetime.utcnow()
                if action_id == "mc_reject":
                    row.status = "rejected"
                    row.rejected_by = slack_subject
                    row.rejected_at = now
                    row.updated_at = now
                    session.add(row)
                    session.commit()
                    emit_controlplane_event(
                        req=request,
                        event_type="approval.rejected",
                        payload={"approval_request_id": row.id, "mission_id": row.mission_id, "action": row.action, "rejected_by": slack_subject},
                    )
                    return {"ok": True, "text": f"Rejected approval request #{row.id}"}

                row.status = "approved"
                row.approved_by = slack_subject
                row.approved_at = now
                row.approval_nonce = row.approval_nonce or secrets.token_urlsafe(16)
                row.approval_expires_at = row.approval_expires_at or (now + timedelta(minutes=15))
                row.updated_at = now
                session.add(row)
                session.commit()
                session.refresh(row)
                token_payload = {
                    "approval_request_id": row.id,
                    "mission_id": row.mission_id,
                    "action": row.action,
                    "request_id": f"approval-{row.id}",
                    "approved_by": row.approved_by,
                    "approved_at": row.approved_at.isoformat(),
                    "nonce": row.approval_nonce,
                    "exp": int(row.approval_expires_at.timestamp()),
                }
                token = generate_approval_token(token_payload)
                emit_controlplane_event(
                    req=request,
                    event_type="approval.approved",
                    payload={"approval_request_id": row.id, "mission_id": row.mission_id, "action": row.action, "approved_by": slack_subject},
                )
                return {"ok": True, "text": f"Approved #{row.id}. approval_token={token}"}

    emit_controlplane_event(
        req=request,
        event_type="slack.interaction.received",
        payload={
            "type": (payload or {}).get("type", ""),
            "channel_id": channel_id,
            "mission_id": mission_id,
        },
    )
    return {"ok": True}
