"""Tests for Claude Code native hooks endpoints."""

import json
import unittest
from types import SimpleNamespace

from sqlmodel import SQLModel, select

from app.db import engine, get_session
from app.models import Agent, AgentSession
from app.routers.hooks import hook_session_start, hook_session_end, hook_tool_audit


def _request(subject: str):
    return SimpleNamespace(
        state=SimpleNamespace(principal={"subject": subject, "email": subject})
    )


def _make_agent(name: str) -> Agent:
    with get_session() as db:
        agent = Agent(name=name, capabilities="test")
        db.add(agent)
        db.commit()
        db.refresh(agent)
        return agent


class TestHooks(unittest.TestCase):
    def setUp(self):
        SQLModel.metadata.drop_all(engine, checkfirst=True)
        SQLModel.metadata.create_all(engine)

    # ── session-start ────────────────────────────────────────────────────────

    def test_session_start_creates_session(self):
        _make_agent("alice@test.com")
        payload = {"session_id": "sess-abc", "source": "startup"}
        result = hook_session_start(payload, _request("alice@test.com"))
        self.assertIn("sess-abc", result)
        self.assertIn("alice@test.com", result)

        with get_session() as db:
            sess = db.exec(
                select(AgentSession).where(AgentSession.claude_session_id == "sess-abc")
            ).first()
        self.assertIsNotNone(sess)
        self.assertIsNone(sess.ended_at)

    def test_session_start_idempotent(self):
        """Calling session-start twice for same claude_session_id does not duplicate."""
        _make_agent("bob@test.com")
        payload = {"session_id": "sess-dup", "source": "startup"}
        hook_session_start(payload, _request("bob@test.com"))
        hook_session_start(payload, _request("bob@test.com"))

        with get_session() as db:
            sessions = db.exec(
                select(AgentSession).where(AgentSession.claude_session_id == "sess-dup")
            ).all()
        self.assertEqual(len(sessions), 1)

    def test_session_start_compact_source(self):
        """compact source is accepted and returns context."""
        _make_agent("carol@test.com")
        payload = {"session_id": "sess-compact", "source": "compact"}
        result = hook_session_start(payload, _request("carol@test.com"))
        self.assertIn("compact", result)

    def test_session_start_auto_registers_agent(self):
        """Unknown subject gets auto-registered as an Agent on first contact."""
        payload = {"session_id": "sess-noagent", "source": "startup"}
        result = hook_session_start(payload, _request("unknown@test.com"))
        self.assertIn("unknown@test.com", result)

        with get_session() as db:
            agent = db.exec(select(Agent).where(Agent.name == "unknown@test.com")).first()
        self.assertIsNotNone(agent)
        self.assertEqual(agent.capabilities, "claude-code")

    # ── session-end ──────────────────────────────────────────────────────────

    def test_session_end_sets_ended_at(self):
        _make_agent("dave@test.com")
        hook_session_start({"session_id": "sess-end", "source": "startup"}, _request("dave@test.com"))

        result = hook_session_end({"session_id": "sess-end", "source": "clear"}, _request("dave@test.com"))
        self.assertEqual(result, {"ok": True})

        with get_session() as db:
            sess = db.exec(
                select(AgentSession).where(AgentSession.claude_session_id == "sess-end")
            ).first()
        self.assertIsNotNone(sess.ended_at)
        self.assertEqual(sess.end_reason, "clear")

    def test_session_end_no_session(self):
        """Missing session_id gracefully returns ok."""
        _make_agent("eve@test.com")
        result = hook_session_end({"session_id": "nonexistent", "source": "clear"}, _request("eve@test.com"))
        self.assertEqual(result, {"ok": True})

    # ── tool-audit ───────────────────────────────────────────────────────────

    def test_tool_audit_appends_entry(self):
        _make_agent("frank@test.com")
        hook_session_start({"session_id": "sess-audit", "source": "startup"}, _request("frank@test.com"))

        hook_tool_audit(
            {
                "session_id": "sess-audit",
                "tool_name": "mcp__missioncontrol__list_missions",
                "tool_input": {"limit": 10},
            },
            _request("frank@test.com"),
        )

        with get_session() as db:
            sess = db.exec(
                select(AgentSession).where(AgentSession.claude_session_id == "sess-audit")
            ).first()

        self.assertIsNotNone(sess)
        lines = [l for l in sess.audit_log.strip().splitlines() if l]
        self.assertEqual(len(lines), 1)
        entry = json.loads(lines[0])
        self.assertEqual(entry["tool"], "mcp__missioncontrol__list_missions")
        self.assertIn("limit", entry["input_summary"])

    def test_tool_audit_multiple_entries(self):
        _make_agent("grace@test.com")
        hook_session_start({"session_id": "sess-multi", "source": "startup"}, _request("grace@test.com"))

        for tool in ["mcp__missioncontrol__get_mission", "mcp__missioncontrol__list_tasks"]:
            hook_tool_audit(
                {"session_id": "sess-multi", "tool_name": tool, "tool_input": {}},
                _request("grace@test.com"),
            )

        with get_session() as db:
            sess = db.exec(
                select(AgentSession).where(AgentSession.claude_session_id == "sess-multi")
            ).first()
        lines = [l for l in sess.audit_log.strip().splitlines() if l]
        self.assertEqual(len(lines), 2)
