from __future__ import annotations

import asyncio
import json
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from sqlmodel import select

from app.db import get_session
from app.models import AiEvent, AiPendingAction, AiSession, AiTurn
from app.routers import mcp as mcp_router
from app.schemas import (
    AiSessionCreate,
    AiSessionRead,
    AiTurnCreate,
)
from app.schemas import MCPCall
from app.services.ai_planner import finalize_assistant_text, is_write_tool, plan_turn
from app.services.ai_views import validate_view_spec
from app.services.ids import new_hash_id

router = APIRouter(prefix="/ai/sessions", tags=["ai"])


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


def _emit_event(
    *,
    session,
    session_id: str,
    turn_id: int | None,
    event_type: str,
    payload: dict,
) -> AiEvent:
    event = AiEvent(
        session_id=session_id,
        turn_id=turn_id,
        event_type=event_type,
        payload_json=json.dumps(payload, separators=(",", ":"), default=str),
        created_at=datetime.utcnow(),
    )
    session.add(event)
    session.commit()
    session.refresh(event)
    return event


def _get_session_or_404(*, session, session_id: str, subject: str) -> AiSession:
    row = session.get(AiSession, session_id)
    if row is None or row.owner_subject != subject:
        raise HTTPException(status_code=404, detail="AI session not found")
    return row


@router.post("", response_model=AiSessionRead)
def create_session(payload: AiSessionCreate, request: Request):
    subject = _subject(request)
    session_id = f"ais_{new_hash_id()}"
    now = datetime.utcnow()
    title = (payload.title or "").strip() or "AI Console Session"
    with get_session() as db:
        row = AiSession(
            id=session_id,
            owner_subject=subject,
            title=title,
            status="active",
            created_at=now,
            updated_at=now,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        _emit_event(
            session=db,
            session_id=row.id,
            turn_id=None,
            event_type="session_started",
            payload={"session_id": row.id, "title": row.title},
        )
        return {
            "id": row.id,
            "owner_subject": row.owner_subject,
            "title": row.title,
            "status": row.status,
            "turns": [],
            "events": [
                _serialize_event(item)
                for item in db.exec(select(AiEvent).where(AiEvent.session_id == row.id).order_by(AiEvent.id.asc())).all()
            ],
            "pending_actions": [],
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }


@router.get("", response_model=list[AiSessionRead])
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
        out = []
        for row in sessions:
            out.append(
                {
                    "id": row.id,
                    "owner_subject": row.owner_subject,
                    "title": row.title,
                    "status": row.status,
                    "turns": [],
                    "events": [],
                    "pending_actions": [],
                    "created_at": row.created_at,
                    "updated_at": row.updated_at,
                }
            )
        return out


@router.get("/{session_id}", response_model=AiSessionRead)
def get_ai_session(session_id: str, request: Request, since_event_id: int = 0, limit: int = 200):
    subject = _subject(request)
    safe_limit = max(1, min(limit, 500))
    with get_session() as db:
        row = _get_session_or_404(session=db, session_id=session_id, subject=subject)
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
            "turns": [_serialize_turn(item) for item in turns],
            "events": [_serialize_event(item) for item in events],
            "pending_actions": [_serialize_pending(item) for item in pending],
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }


@router.post("/{session_id}/turns", response_model=AiSessionRead)
def create_turn(session_id: str, payload: AiTurnCreate, request: Request):
    subject = _subject(request)
    message = (payload.message or "").strip()
    if not message:
        raise HTTPException(status_code=422, detail="message is required")

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
        _emit_event(
            session=db,
            session_id=row.id,
            turn_id=user_turn.id,
            event_type="user_message",
            payload={"text": message},
        )

        plan = plan_turn(message=message)
        _emit_event(
            session=db,
            session_id=row.id,
            turn_id=user_turn.id,
            event_type="planner_result",
            payload={
                "assistant_text": plan.assistant_text,
                "tool_calls": [{"tool": c.tool, "args": c.args, "reason": c.reason} for c in plan.tool_calls],
            },
        )

        executed_results: list[dict] = []
        for call in plan.tool_calls:
            _emit_event(
                session=db,
                session_id=row.id,
                turn_id=user_turn.id,
                event_type="tool_call",
                payload={"tool": call.tool, "args": call.args, "reason": call.reason},
            )
            if is_write_tool(call.tool):
                pending = AiPendingAction(
                    id=f"aia_{new_hash_id()}",
                    session_id=row.id,
                    turn_id=user_turn.id,
                    tool=call.tool,
                    args_json=json.dumps(call.args, separators=(",", ":")),
                    reason=call.reason,
                    status="pending",
                    requested_by=subject,
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                )
                db.add(pending)
                db.commit()
                db.refresh(pending)
                _emit_event(
                    session=db,
                    session_id=row.id,
                    turn_id=user_turn.id,
                    event_type="approval_required",
                    payload={
                        "action_id": pending.id,
                        "tool": pending.tool,
                        "args": call.args,
                        "reason": call.reason,
                    },
                )
                continue

            mcp_result = _call_mcp_tool(tool=call.tool, args=call.args, request=request)
            executed_results.append({"tool": call.tool, "result": mcp_result})
            _emit_event(
                session=db,
                session_id=row.id,
                turn_id=user_turn.id,
                event_type="tool_result",
                payload={"tool": call.tool, "result": mcp_result},
            )

        if plan.view_spec is not None:
            ok, err = validate_view_spec(plan.view_spec)
            if ok:
                _emit_event(
                    session=db,
                    session_id=row.id,
                    turn_id=user_turn.id,
                    event_type="view_rendered",
                    payload={"view": plan.view_spec},
                )
            else:
                _emit_event(
                    session=db,
                    session_id=row.id,
                    turn_id=user_turn.id,
                    event_type="view_rejected",
                    payload={"error": err},
                )

        final_text = finalize_assistant_text(
            message=message,
            draft_text=plan.assistant_text,
            tool_results=executed_results,
        )

        assistant_turn = AiTurn(
            session_id=row.id,
            role="assistant",
            content_json=json.dumps({"text": final_text}, separators=(",", ":")),
            created_at=datetime.utcnow(),
        )
        db.add(assistant_turn)
        row.updated_at = datetime.utcnow()
        db.add(row)
        db.commit()

    return get_ai_session(session_id=session_id, request=request)


@router.post("/{session_id}/actions/{action_id}/approve", response_model=AiSessionRead)
def approve_action(session_id: str, action_id: str, request: Request):
    subject = _subject(request)
    with get_session() as db:
        _get_session_or_404(session=db, session_id=session_id, subject=subject)
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

        _emit_event(
            session=db,
            session_id=session_id,
            turn_id=action.turn_id,
            event_type="approval_outcome",
            payload={"action_id": action.id, "status": "approved"},
        )

        args = _decode_json(action.args_json)
        mcp_result = _call_mcp_tool(tool=action.tool, args=args, request=request)
        action.status = "executed"
        action.updated_at = datetime.utcnow()
        db.add(action)
        db.commit()
        _emit_event(
            session=db,
            session_id=session_id,
            turn_id=action.turn_id,
            event_type="tool_result",
            payload={"tool": action.tool, "result": mcp_result},
        )

    return get_ai_session(session_id=session_id, request=request)


@router.post("/{session_id}/actions/{action_id}/reject", response_model=AiSessionRead)
def reject_action(session_id: str, action_id: str, request: Request, note: str = ""):
    subject = _subject(request)
    with get_session() as db:
        _get_session_or_404(session=db, session_id=session_id, subject=subject)
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

        _emit_event(
            session=db,
            session_id=session_id,
            turn_id=action.turn_id,
            event_type="approval_outcome",
            payload={"action_id": action.id, "status": "rejected", "note": note or ""},
        )

    return get_ai_session(session_id=session_id, request=request)


@router.get("/{session_id}/stream")
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
                    payload = {
                        "id": row.id,
                        "turn_id": row.turn_id,
                        "event_type": row.event_type,
                        "payload": _decode_json(row.payload_json),
                        "created_at": row.created_at.isoformat() + "Z",
                    }
                    yield f"id: {row.id}\n"
                    yield f"event: ai_event\n"
                    yield f"data: {json.dumps(payload, separators=(',', ':'))}\n\n"
            await asyncio.sleep(0.8)

    return StreamingResponse(generator(), media_type="text/event-stream")


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
