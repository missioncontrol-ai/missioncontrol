import asyncio
import json
import os
import unittest
from types import SimpleNamespace
from urllib.parse import urlencode
from unittest.mock import patch

from sqlmodel import SQLModel

from app.db import engine, get_session
from app.models import ApprovalRequest, Kluster, Mission, MissionRoleMembership
from app.routers.slack_integrations import slack_commands, slack_events, slack_interactions


class _Req:
    def __init__(self, body: bytes, headers: dict | None = None):
        self._body = body
        self.headers = headers or {}
        self.app = SimpleNamespace(state=SimpleNamespace(mqtt=None))

    async def body(self):
        return self._body


class SlackWorkflowTests(unittest.TestCase):
    def setUp(self):
        SQLModel.metadata.drop_all(engine)
        SQLModel.metadata.create_all(engine)
        with get_session() as session:
            mission = Mission(id="mission-a", name="mission-a", owners="owner@example.com")
            session.add(mission)
            session.add(Kluster(id="kluster-a", mission_id="mission-a", name="kluster-a"))
            session.add(
                MissionRoleMembership(
                    mission_id="mission-a",
                    subject="slack:UCONTRIB",
                    role="mission_contributor",
                )
            )
            session.add(
                MissionRoleMembership(
                    mission_id="mission-a",
                    subject="slack:UOWNER",
                    role="mission_owner",
                )
            )
            session.commit()

    def test_task_create_command_creates_task(self):
        with get_session() as session:
            # Add binding required by command path.
            from app.models import SlackChannelBinding

            session.add(SlackChannelBinding(mission_id="mission-a", channel_id="C1", channel_name="ops"))
            session.commit()

        body = urlencode(
            {
                "command": "/mc",
                "text": 'task.create mission_id=mission-a kluster_id=kluster-a title="Ship API"',
                "channel_id": "C1",
                "user_id": "UCONTRIB",
            }
        ).encode("utf-8")
        with patch("app.routers.slack_integrations.slack_provider.verify", return_value=(True, "ok")):
            with patch("app.routers.slack_integrations.index_task", return_value=None):
                with patch("app.routers.slack_integrations.score_overlap_vector", return_value=[]):
                    response = asyncio.run(slack_commands(_Req(body)))
        self.assertIn("Created task #", response.get("text", ""))
        self.assertIn("blocks", response)

    def test_interaction_approve_returns_token(self):
        with get_session() as session:
            from app.models import SlackChannelBinding

            session.add(SlackChannelBinding(mission_id="mission-a", channel_id="C1", channel_name="ops"))
            approval = ApprovalRequest(
                mission_id="mission-a",
                action="task.update",
                status="pending",
                requested_by="slack:UCONTRIB",
            )
            session.add(approval)
            session.commit()
            session.refresh(approval)
            approval_id = approval.id

        payload = {
            "type": "block_actions",
            "mission_id": "mission-a",
            "channel": {"id": "C1"},
            "user": {"id": "UOWNER"},
            "actions": [{"action_id": "mc_approve", "value": str(approval_id)}],
        }
        body = urlencode({"payload": json.dumps(payload)}).encode("utf-8")
        with patch.dict(os.environ, {"MC_APPROVAL_TOKEN_SECRET": "test-secret"}, clear=False):
            with patch("app.routers.slack_integrations.slack_provider.verify", return_value=(True, "ok")):
                response = asyncio.run(slack_interactions(_Req(body)))
        self.assertTrue(response.get("ok"))
        self.assertIn("approval_token=", response.get("text", ""))

    def test_approval_request_command_returns_in_channel_blocks(self):
        with get_session() as session:
            from app.models import SlackChannelBinding

            session.add(SlackChannelBinding(mission_id="mission-a", channel_id="C1", channel_name="ops"))
            session.commit()
        body = urlencode(
            {
                "command": "/mc",
                "text": "approval.request mission_id=mission-a action=task.update reason='Need change'",
                "channel_id": "C1",
                "user_id": "UCONTRIB",
            }
        ).encode("utf-8")
        with patch("app.routers.slack_integrations.slack_provider.verify", return_value=(True, "ok")):
            response = asyncio.run(slack_commands(_Req(body)))
        self.assertEqual(response.get("response_type"), "in_channel")
        self.assertTrue(response.get("blocks"))

    def test_duplicate_command_is_ignored(self):
        with get_session() as session:
            from app.models import SlackChannelBinding

            session.add(SlackChannelBinding(mission_id="mission-a", channel_id="C1", channel_name="ops"))
            session.commit()
        body = urlencode(
            {
                "command": "/mc",
                "text": "search mission_id=mission-a q=ship",
                "channel_id": "C1",
                "user_id": "UCONTRIB",
            }
        ).encode("utf-8")
        with patch("app.routers.slack_integrations.slack_provider.verify", return_value=(True, "ok")):
            _ = asyncio.run(slack_commands(_Req(body)))
            replay = asyncio.run(slack_commands(_Req(body)))
        self.assertIn("Duplicate command", replay.get("text", ""))

    def test_duplicate_interaction_is_ignored(self):
        with get_session() as session:
            from app.models import SlackChannelBinding

            session.add(SlackChannelBinding(mission_id="mission-a", channel_id="C1", channel_name="ops"))
            approval = ApprovalRequest(
                mission_id="mission-a",
                action="task.update",
                status="pending",
                requested_by="slack:UCONTRIB",
            )
            session.add(approval)
            session.commit()
            session.refresh(approval)
            approval_id = approval.id

        payload = {
            "type": "block_actions",
            "mission_id": "mission-a",
            "channel": {"id": "C1"},
            "user": {"id": "UOWNER"},
            "actions": [{"action_id": "mc_reject", "value": str(approval_id)}],
        }
        body = urlencode({"payload": json.dumps(payload)}).encode("utf-8")
        with patch("app.routers.slack_integrations.slack_provider.verify", return_value=(True, "ok")):
            first = asyncio.run(slack_interactions(_Req(body)))
            second = asyncio.run(slack_interactions(_Req(body)))
        self.assertTrue(first.get("ok"))
        self.assertTrue(second.get("duplicate"))

    def test_duplicate_event_is_ignored(self):
        with get_session() as session:
            from app.models import SlackChannelBinding

            session.add(SlackChannelBinding(mission_id="mission-a", channel_id="C1", channel_name="ops"))
            session.commit()
        payload = {
            "type": "event_callback",
            "mission_id": "mission-a",
            "event": {"type": "app_mention", "channel": "C1"},
        }
        body = json.dumps(payload).encode("utf-8")
        with patch("app.routers.slack_integrations.slack_provider.verify", return_value=(True, "ok")):
            first = asyncio.run(slack_events(_Req(body)))
            second = asyncio.run(slack_events(_Req(body)))
        self.assertTrue(first.get("ok"))
        self.assertTrue(second.get("duplicate"))


if __name__ == "__main__":
    unittest.main()
