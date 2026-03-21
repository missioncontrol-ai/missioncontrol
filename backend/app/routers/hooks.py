"""Lifecycle hook endpoints called by Claude Code native hooks.

These endpoints are invoked by Claude Code's built-in hooks system (injected per-instance
by `mc launch`) to register sessions, close them, and audit MCP tool calls.
"""

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


def _find_or_create_agent(session, subject: str) -> Agent:
    """Return the Agent for this subject, creating it on first contact."""
    agent = session.exec(select(Agent).where(Agent.name == subject)).first()
    if agent is None:
        agent = Agent(name=subject, capabilities="claude-code", status="offline")
        session.add(agent)
        session.commit()
        session.refresh(agent)
    return agent


@router.post("/session-start", response_class=PlainTextResponse)
def hook_session_start(payload: dict[str, Any], request: Request) -> str:
    """Called on SessionStart (startup/resume/compact).

    Creates or updates an AgentSession keyed by claude_session_id.
    Returns a plain-text context block that Claude Code injects into its context window.
    """
    subject = actor_subject_from_request(request)
    claude_session_id = str(payload.get("session_id") or "")
    source = str(payload.get("source") or payload.get("hook_event_name") or "")

    with get_session() as db:
        agent = _find_or_create_agent(db, subject)

        if claude_session_id:
            # Upsert: find existing open session for this claude_session_id, or create new.
            existing = db.exec(
                select(AgentSession)
                .where(AgentSession.claude_session_id == claude_session_id)
                .where(AgentSession.ended_at == None)  # noqa: E711
            ).first()

            if existing is None:
                sess = AgentSession(
                    agent_id=agent.id,
                    context=source,
                    claude_session_id=claude_session_id,
                )
                db.add(sess)
                agent.status = "online"
                db.add(agent)
                db.commit()

        context_lines = [
            f"[MC Session — {claude_session_id or 'unknown'}]",
            f"Agent: {subject}",
            f"Source: {source}",
            f"Registered: {datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')}",
            f"Agent ID: {agent.id}",
            f"Capabilities: {agent.capabilities or 'none'}",
        ]

    return "\n".join(context_lines)


@router.post("/session-end")
def hook_session_end(payload: dict[str, Any], request: Request) -> dict:
    """Called on SessionEnd. Closes the AgentSession."""
    subject = actor_subject_from_request(request)
    claude_session_id = str(payload.get("session_id") or "")
    end_reason = str(payload.get("source") or payload.get("hook_event_name") or "")

    with get_session() as db:
        agent = _find_or_create_agent(db, subject)
        if claude_session_id:
            sess = db.exec(
                select(AgentSession)
                .where(AgentSession.claude_session_id == claude_session_id)
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


@router.post("/tool-audit")
def hook_tool_audit(payload: dict[str, Any], request: Request) -> dict:
    """Called on PostToolUse for mcp__missioncontrol__* tools.

    Appends a JSON-lines audit entry to AgentSession.audit_log.
    """
    subject = actor_subject_from_request(request)
    claude_session_id = str(payload.get("session_id") or "")
    tool_name = str(payload.get("tool_name") or "")
    tool_input = payload.get("tool_input") or {}

    entry = {
        "ts": datetime.utcnow().isoformat(),
        "tool": tool_name,
        "input_summary": json.dumps(tool_input)[:512],
    }
    entry_line = json.dumps(entry)

    with get_session() as db:
        agent = _find_or_create_agent(db, subject)
        if claude_session_id:
            sess = db.exec(
                select(AgentSession)
                .where(AgentSession.claude_session_id == claude_session_id)
                .where(AgentSession.agent_id == agent.id)
                .where(AgentSession.ended_at == None)  # noqa: E711
            ).first()
            if sess:
                sess.audit_log = (sess.audit_log or "") + entry_line + "\n"
                db.add(sess)
                db.commit()

    return {"ok": True}
