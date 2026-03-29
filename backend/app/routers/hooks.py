"""Lifecycle hook endpoints for agent runtimes (Claude + Codex)."""

import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse
from sqlmodel import select

from app.db import get_session
from app.models import Agent, AgentSession
from app.services.authz import actor_subject_from_request

router = APIRouter(prefix="/hooks/claude", tags=["hooks"])
codex_router = APIRouter(prefix="/hooks/codex", tags=["hooks"])


def _find_or_create_agent(session, subject: str, capability: str) -> Agent:
    """Return the Agent for this subject, creating it on first contact."""
    agent = session.exec(select(Agent).where(Agent.name == subject)).first()
    if agent is None:
        agent = Agent(name=subject, capabilities=capability, status="offline")
        session.add(agent)
        session.commit()
        session.refresh(agent)
    return agent


def _session_start(payload: dict[str, Any], request: Request, *, capability: str) -> str:
    subject = actor_subject_from_request(request)
    session_id = str(payload.get("session_id") or "")
    source = str(payload.get("source") or payload.get("hook_event_name") or "")

    with get_session() as db:
        agent = _find_or_create_agent(db, subject, capability)

        if session_id:
            existing = db.exec(
                select(AgentSession)
                .where(AgentSession.claude_session_id == session_id)
                .where(AgentSession.ended_at == None)  # noqa: E711
            ).first()

            if existing is None:
                sess = AgentSession(
                    agent_id=agent.id,
                    context=source,
                    claude_session_id=session_id,
                )
                db.add(sess)
                agent.status = "online"
                db.add(agent)
                db.commit()

        context_lines = [
            f"[MC Session — {session_id or 'unknown'}]",
            f"Agent: {subject}",
            f"Source: {source}",
            f"Registered: {datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')}",
            f"Agent ID: {agent.id}",
            f"Capabilities: {agent.capabilities or 'none'}",
        ]
    return "\n".join(context_lines)


def _session_end(payload: dict[str, Any], request: Request, *, capability: str) -> dict:
    subject = actor_subject_from_request(request)
    session_id = str(payload.get("session_id") or "")
    end_reason = str(payload.get("source") or payload.get("hook_event_name") or "")

    with get_session() as db:
        agent = _find_or_create_agent(db, subject, capability)
        if session_id:
            sess = db.exec(
                select(AgentSession)
                .where(AgentSession.claude_session_id == session_id)
                .where(AgentSession.agent_id == agent.id)
                .where(AgentSession.ended_at == None)  # noqa: E711
            ).first()
            if sess:
                sess.ended_at = datetime.utcnow()
                sess.end_reason = end_reason or "session_end"
                agent.status = "offline"
                db.add(sess)
                db.add(agent)
                db.commit()

    return {"ok": True}


def _tool_audit(payload: dict[str, Any], request: Request, *, capability: str) -> dict:
    subject = actor_subject_from_request(request)
    session_id = str(payload.get("session_id") or "")
    tool_name = str(payload.get("tool_name") or "")
    tool_input = payload.get("tool_input") or {}

    entry = {
        "ts": datetime.utcnow().isoformat(),
        "tool": tool_name,
        "runtime": capability,
        "input_summary": json.dumps(tool_input)[:512],
    }
    entry_line = json.dumps(entry)

    with get_session() as db:
        agent = _find_or_create_agent(db, subject, capability)
        if session_id:
            sess = db.exec(
                select(AgentSession)
                .where(AgentSession.claude_session_id == session_id)
                .where(AgentSession.agent_id == agent.id)
                .where(AgentSession.ended_at == None)  # noqa: E711
            ).first()
            if sess:
                sess.audit_log = (sess.audit_log or "") + entry_line + "\n"
                db.add(sess)
                db.commit()

    return {"ok": True}


@router.post("/session-start", response_class=PlainTextResponse)
def hook_session_start(payload: dict[str, Any], request: Request) -> str:
    return _session_start(payload, request, capability="claude-code")


@router.post("/session-end")
def hook_session_end(payload: dict[str, Any], request: Request) -> dict:
    return _session_end(payload, request, capability="claude-code")


@router.post("/tool-audit")
def hook_tool_audit(payload: dict[str, Any], request: Request) -> dict:
    return _tool_audit(payload, request, capability="claude-code")


@codex_router.post("/session-start", response_class=PlainTextResponse)
def hook_codex_session_start(payload: dict[str, Any], request: Request) -> str:
    return _session_start(payload, request, capability="codex-sdk")


@codex_router.post("/session-end")
def hook_codex_session_end(payload: dict[str, Any], request: Request) -> dict:
    return _session_end(payload, request, capability="codex-sdk")


@codex_router.post("/tool-audit")
def hook_codex_tool_audit(payload: dict[str, Any], request: Request) -> dict:
    return _tool_audit(payload, request, capability="codex-sdk")
