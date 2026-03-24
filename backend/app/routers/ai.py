from __future__ import annotations

import asyncio
import json
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from sqlmodel import select

from app.ai_console.event_store import emit_event as _store_emit_event
from app.ai_console.gateway import get_gateway
from app.ai_console.registry import available_runtimes
from app.ai_console.runtime_config import normalize_runtime_kind
from app.db import get_session
from app.models import AiEvent, AiPendingAction, AiSession, AiTurn
from app.routers import mcp as mcp_router
from app.schemas import (
    AiSessionCreate,
    AiSessionRead,
    AiTurnCreate,
)
from app.schemas import MCPCall
from app.services.ids import new_hash_id

router = APIRouter(prefix="/ai", tags=["ai"])


def _subject(request: Request) -> str:
    principal = getattr(getattr(request, "state", None), "principal", None)
    if isinstance(principal, dict):
        subject = str(principal.get("email") or principal.get("subject") or "").strip().lower()
        if subject:
            return subject
    raise HTTPException(status_code=401, detail="Unauthorized")


def _decode_json(raw: str) -> dict:
    try:
        parsed = json.loads(raw or "{}")
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass
    return {}


def _serialize_turn(row: AiTurn) -> dict:
    return {
        "id": row.id,
        "role": row.role,
        "content": _decode_json(row.content_json),
        "created_at": row.created_at,
    }


def _serialize_event(row: AiEvent) -> dict:
    return {
        "id": row.id,
        "turn_id": row.turn_id,
        "event_type": row.event_type,
        "payload": _decode_json(row.payload_json),
        "created_at": row.created_at,
    }


def _serialize_pending(row: AiPendingAction) -> dict:
    return {
        "id": row.id,
        "tool": row.tool,
        "args": _decode_json(row.args_json),
        "reason": row.reason,
        "status": row.status,
        "requested_by": row.requested_by,
        "approved_by": row.approved_by,
        "rejected_by": row.rejected_by,
        "rejection_note": row.rejection_note,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _serialize_session(*, db, row: AiSession, since_event_id: int = 0, include_turns: bool = True, limit: int = 200) -> dict:
    safe_limit = max(1, min(limit, 500))
    turns = []
    if include_turns:
        turns = db.exec(select(AiTurn).where(AiTurn.session_id == row.id).order_by(AiTurn.id.asc()).limit(safe_limit)).all()

    events_stmt = select(AiEvent).where(AiEvent.session_id == row.id)
    if since_event_id > 0:
        events_stmt = events_stmt.where(AiEvent.id > since_event_id)
    events = db.exec(events_stmt.order_by(AiEvent.id.asc()).limit(safe_limit)).all()

    pending = db.exec(
        select(AiPendingAction)
        .where(AiPendingAction.session_id == row.id)
        .where(AiPendingAction.status.in_(["pending", "approved"]))
        .order_by(AiPendingAction.created_at.asc())
        .limit(safe_limit)
    ).all()

    return {
        "id": row.id,
        "owner_subject": row.owner_subject,
        "title": row.title,
        "status": row.status,
        "runtime_kind": row.runtime_kind,
        "runtime_session_id": row.runtime_session_id,
        "workspace_path": row.workspace_path,
        "capability_snapshot": _decode_json(row.capability_snapshot_json),
        "policy": _decode_json(row.policy_json),
        "turns": [_serialize_turn(t) for t in turns],
        "events": [_serialize_event(e) for e in events],
        "pending_actions": [_serialize_pending(p) for p in pending],
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _get_session_or_404(*, session, session_id: str, subject: str) -> AiSession:
    row = session.get(AiSession, session_id)
    if row is None or row.owner_subject != subject:
        raise HTTPException(status_code=404, detail="AI session not found")
    return row


# ── Capabilities ──────────────────────────────────────────────────────────────

@router.get("/runtime-capabilities")
def get_runtime_capabilities():
    """List capability sets for all registered runtime adapters."""
    caps = available_runtimes()
    return [
        {
            "runtime_kind": c.runtime_kind.value,
            "display_name": c.display_name,
            "icon_slug": c.icon_slug,
            "supports_streaming": c.supports_streaming,
            "supports_file_workspace": c.supports_file_workspace,
            "supports_tool_interception": c.supports_tool_interception,
            "supports_skill_packs": c.supports_skill_packs,
            "supports_session_resume": c.supports_session_resume,
            "max_context_tokens": c.max_context_tokens,
        }
        for c in caps
    ]


# ── Session CRUD ──────────────────────────────────────────────────────────────

@router.post("/sessions", response_model=AiSessionRead)
async def create_session(payload: AiSessionCreate, request: Request):
    subject = _subject(request)
    session_id = f"ais_{new_hash_id()}"
    title = (payload.title or "").strip() or "AI Console Session"
    runtime_kind = normalize_runtime_kind(payload.runtime_kind).value
    policy_dict = payload.policy or {}

    gateway = get_gateway()
    with get_session() as db:
        row = await gateway.create_session(
            db=db,
            subject=subject,
            session_id=session_id,
            title=title,
            runtime_kind=runtime_kind,
            policy_dict=policy_dict,
        )
        return _serialize_session(db=db, row=row, include_turns=False)


@router.get("/sessions", response_model=list[AiSessionRead])
def list_sessions(request: Request, limit: int = 20):
    subject = _subject(request)
    safe_limit = max(1, min(limit, 100))
    with get_session() as db:
        sessions = db.exec(
            select(AiSession)
            .where(AiSession.owner_subject == subject)
            .order_by(AiSession.updated_at.desc())
            .limit(safe_limit)
        ).all()
        return [
            {
                "id": row.id,
                "owner_subject": row.owner_subject,
                "title": row.title,
                "status": row.status,
                "runtime_kind": row.runtime_kind,
                "runtime_session_id": row.runtime_session_id,
                "workspace_path": row.workspace_path,
                "capability_snapshot": {},
                "policy": {},
                "turns": [],
                "events": [],
                "pending_actions": [],
                "created_at": row.created_at,
                "updated_at": row.updated_at,
            }
            for row in sessions
        ]


@router.get("/sessions/{session_id}", response_model=AiSessionRead)
def get_ai_session(session_id: str, request: Request, since_event_id: int = 0, limit: int = 200):
    subject = _subject(request)
    with get_session() as db:
        row = _get_session_or_404(session=db, session_id=session_id, subject=subject)
        return _serialize_session(db=db, row=row, since_event_id=since_event_id, limit=limit)


@router.post("/sessions/{session_id}/turns", response_model=AiSessionRead)
async def create_turn(session_id: str, payload: AiTurnCreate, request: Request):
    subject = _subject(request)
    message = (payload.message or "").strip()
    if not message:
        raise HTTPException(status_code=422, detail="message is required")

    gateway = get_gateway()
    with get_session() as db:
        row = _get_session_or_404(session=db, session_id=session_id, subject=subject)

        user_turn = AiTurn(
            session_id=row.id,
            role="user",
            content_json=json.dumps({"text": message}, separators=(",", ":")),
            created_at=datetime.utcnow(),
        )
        db.add(user_turn)
        db.commit()
        db.refresh(user_turn)

        _store_emit_event(
            db=db,
            session_id=row.id,
            turn_id=user_turn.id,
            event_type="user_message",
            payload={"text": message},
        )

        await gateway.process_turn(
            db=db,
            session_row=row,
            user_turn=user_turn,
            message=message,
            subject=subject,
        )

        row.updated_at = datetime.utcnow()
        db.add(row)
        db.commit()

    with get_session() as db:
        row = db.get(AiSession, session_id)
        return _serialize_session(db=db, row=row)


# ── Approval flow ─────────────────────────────────────────────────────────────

@router.post("/sessions/{session_id}/actions/{action_id}/approve", response_model=AiSessionRead)
async def approve_action(session_id: str, action_id: str, request: Request):
    subject = _subject(request)
    gateway = get_gateway()
    with get_session() as db:
        session_row = _get_session_or_404(session=db, session_id=session_id, subject=subject)
        action = db.get(AiPendingAction, action_id)
        if action is None or action.session_id != session_id:
            raise HTTPException(status_code=404, detail="AI action not found")
        if action.status != "pending":
            raise HTTPException(status_code=409, detail="Action is not pending")

        action.status = "approved"
        action.approved_by = subject
        action.updated_at = datetime.utcnow()
        db.add(action)
        db.commit()

        _store_emit_event(
            db=db,
            session_id=session_id,
            turn_id=action.turn_id,
            event_type="approval_outcome",
            payload={"action_id": action.id, "status": "approved"},
        )

        # Notify the runtime adapter (opencode will execute the tool on its side)
        await gateway.approve_action(
            db=db,
            session_row=session_row,
            action_row=action,
            subject=subject,
        )

        # For sessions without a runtime (MC-native tool execution), run via MCP
        if not session_row.runtime_session_id:
            args = _decode_json(action.args_json)
            mcp_result = _call_mcp_tool(tool=action.tool, args=args, request=request)
            action.status = "executed"
            action.updated_at = datetime.utcnow()
            db.add(action)
            db.commit()
            _store_emit_event(
                db=db,
                session_id=session_id,
                turn_id=action.turn_id,
                event_type="tool_result",
                payload={"tool": action.tool, "result": mcp_result},
            )
        else:
            action.status = "executed"
            action.updated_at = datetime.utcnow()
            db.add(action)
            db.commit()

    with get_session() as db:
        row = db.get(AiSession, session_id)
        return _serialize_session(db=db, row=row)


@router.post("/sessions/{session_id}/actions/{action_id}/reject", response_model=AiSessionRead)
async def reject_action(session_id: str, action_id: str, request: Request, note: str = ""):
    subject = _subject(request)
    gateway = get_gateway()
    with get_session() as db:
        session_row = _get_session_or_404(session=db, session_id=session_id, subject=subject)
        action = db.get(AiPendingAction, action_id)
        if action is None or action.session_id != session_id:
            raise HTTPException(status_code=404, detail="AI action not found")
        if action.status != "pending":
            raise HTTPException(status_code=409, detail="Action is not pending")

        action.status = "rejected"
        action.rejected_by = subject
        action.rejection_note = note or ""
        action.updated_at = datetime.utcnow()
        db.add(action)
        db.commit()

        _store_emit_event(
            db=db,
            session_id=session_id,
            turn_id=action.turn_id,
            event_type="approval_outcome",
            payload={"action_id": action.id, "status": "rejected", "note": note or ""},
        )

        await gateway.reject_action(
            db=db,
            session_row=session_row,
            action_row=action,
            subject=subject,
            reason=note or "",
        )

    with get_session() as db:
        row = db.get(AiSession, session_id)
        return _serialize_session(db=db, row=row)


# ── SSE stream ────────────────────────────────────────────────────────────────

@router.get("/sessions/{session_id}/stream")
async def stream_events(session_id: str, request: Request, after_id: int = 0):
    subject = _subject(request)
    with get_session() as db:
        _get_session_or_404(session=db, session_id=session_id, subject=subject)

    async def generator():
        last = max(0, after_id)
        deadline = datetime.utcnow().timestamp() + 30
        while datetime.utcnow().timestamp() < deadline:
            with get_session() as db:
                rows = db.exec(
                    select(AiEvent)
                    .where(AiEvent.session_id == session_id)
                    .where(AiEvent.id > last)
                    .order_by(AiEvent.id.asc())
                    .limit(100)
                ).all()
                for row in rows:
                    last = max(last, int(row.id or 0))
                    data = {
                        "id": row.id,
                        "turn_id": row.turn_id,
                        "event_type": row.event_type,
                        "payload": _decode_json(row.payload_json),
                        "created_at": row.created_at.isoformat() + "Z",
                    }
                    yield f"id: {row.id}\n"
                    yield f"event: ai_event\n"
                    yield f"data: {json.dumps(data, separators=(',', ':'))}\n\n"
            await asyncio.sleep(0.8)

    return StreamingResponse(generator(), media_type="text/event-stream")


# ── Internal helpers ──────────────────────────────────────────────────────────

def _call_mcp_tool(*, tool: str, args: dict, request: Request) -> dict:
    response = Response()
    try:
        out = mcp_router.call_tool(MCPCall(tool=tool, args=args), request, response)
        if hasattr(out, "model_dump"):
            return out.model_dump()
        if hasattr(out, "dict"):
            return out.dict()
        return {"ok": False, "error": "unexpected_response", "result": {}}
    except HTTPException as exc:
        return {"ok": False, "error": str(exc.detail), "result": {}, "status_code": exc.status_code}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "result": {}}
