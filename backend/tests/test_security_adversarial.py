import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException, Response
from sqlmodel import SQLModel

from app.db import engine, get_session
from app.models import Doc, Kluster, Mission, MissionRoleMembership, Task
from app.routers.google_chat_integrations import google_chat_events
from app.routers.mcp import call_tool
from app.routers.teams_integrations import teams_events
from app.schemas import MCPCall


class _WebhookReq:
    def __init__(self, body: bytes, headers: dict | None = None):
        self._body = body
        self.headers = headers or {}
        self.app = SimpleNamespace(state=SimpleNamespace(mqtt=None))

    async def body(self):
        return self._body


def _mcp_request(email: str = "viewer@example.com", subject: str = "oidc-subject"):
    return SimpleNamespace(
        state=SimpleNamespace(principal={"email": email, "subject": subject}),
        headers={},
        url=SimpleNamespace(path="/mcp/call"),
    )


class SecurityAdversarialTests(unittest.TestCase):
    def setUp(self):
        SQLModel.metadata.drop_all(engine)
        SQLModel.metadata.create_all(engine)
        with get_session() as session:
            session.add(Mission(id="mission-a", name="mission-a", owners="owner-a@example.com"))
            session.add(Mission(id="mission-b", name="mission-b", owners="owner-b@example.com"))
            session.add(Kluster(id="kluster-a", mission_id="mission-a", name="kluster-a"))
            session.add(Kluster(id="kluster-b", mission_id="mission-b", name="kluster-b"))
            session.add(MissionRoleMembership(mission_id="mission-a", subject="viewer@example.com", role="mission_viewer"))
            task_a = Task(kluster_id="kluster-a", title="task-a")
            task_b = Task(kluster_id="kluster-b", title="task-b")
            doc_a = Doc(kluster_id="kluster-a", title="doc-a", body="safe")
            doc_b = Doc(kluster_id="kluster-b", title="doc-b", body="secret")
            session.add(task_a)
            session.add(task_b)
            session.add(doc_a)
            session.add(doc_b)
            session.commit()
            session.refresh(task_a)
            session.refresh(task_b)
            session.refresh(doc_a)
            session.refresh(doc_b)
            self.task_a_id = task_a.id
            self.task_b_id = task_b.id
            self.doc_b_id = doc_b.id

    def test_mcp_search_filters_cross_mission_leak_attempt(self):
        req = _mcp_request()
        payload = MCPCall(tool="search_tasks", args={"query": "task", "limit": 10})
        with patch("app.routers.mcp.query_tasks") as mock_query:
            mock_query.return_value = [
                {"id": self.task_a_id, "distance": 0.1},
                {"id": self.task_b_id, "distance": 0.2},
            ]
            response = call_tool(payload, req, Response())
        self.assertTrue(response.ok)
        task_ids = [item["id"] for item in response.result.get("tasks", [])]
        self.assertEqual(task_ids, [self.task_a_id])

    def test_mcp_read_doc_blocks_cross_mission_access(self):
        req = _mcp_request()
        payload = MCPCall(tool="read_doc", args={"doc_id": self.doc_b_id})
        response = call_tool(payload, req, Response())
        self.assertFalse(response.ok)
        self.assertIn("Forbidden", response.error or "")

    def test_google_chat_invalid_token_rejected(self):
        payload = {"type": "MESSAGE", "mission_id": "mission-a", "space": {"name": "spaces/AAA"}}
        req = _WebhookReq(json.dumps(payload).encode("utf-8"), headers={"X-Goog-Chat-Token": "bad"})
        with patch.dict("os.environ", {"GOOGLE_CHAT_VERIFICATION_TOKEN": "good"}, clear=False):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(google_chat_events(req))
        self.assertEqual(ctx.exception.status_code, 401)

    def test_teams_invalid_token_rejected(self):
        payload = {"type": "message", "mission_id": "mission-a", "channel_id": "19:abc@thread.tacv2"}
        req = _WebhookReq(json.dumps(payload).encode("utf-8"), headers={"X-MissionControl-Teams-Token": "bad"})
        with patch.dict("os.environ", {"TEAMS_VERIFICATION_TOKEN": "good"}, clear=False):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(teams_events(req))
        self.assertEqual(ctx.exception.status_code, 401)


if __name__ == "__main__":
    unittest.main()
