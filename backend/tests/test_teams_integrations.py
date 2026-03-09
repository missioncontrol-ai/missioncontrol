import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from sqlmodel import SQLModel

from app.db import engine, get_session
from app.models import Mission, SlackChannelBinding
from app.routers.teams_integrations import teams_events


class _Req:
    def __init__(self, body: bytes, headers: dict | None = None):
        self._body = body
        self.headers = headers or {}
        self.app = SimpleNamespace(state=SimpleNamespace(mqtt=None))

    async def body(self):
        return self._body


class TeamsIntegrationTests(unittest.TestCase):
    def setUp(self):
        SQLModel.metadata.drop_all(engine)
        SQLModel.metadata.create_all(engine)
        with get_session() as session:
            session.add(Mission(id="mission-a", name="mission-a", owners="owner@example.com"))
            session.add(
                SlackChannelBinding(
                    provider="teams",
                    mission_id="mission-a",
                    channel_id="19:abc123@thread.tacv2",
                    channel_name="ops-channel",
                    channel_metadata_json=json.dumps({"webhook_url": "https://outlook.office.com/webhook/..."}),
                )
            )
            session.commit()

    def test_teams_event_dedupes(self):
        payload = {
            "type": "message",
            "mission_id": "mission-a",
            "channel_id": "19:abc123@thread.tacv2",
        }
        body = json.dumps(payload).encode("utf-8")
        req = _Req(body, headers={"X-MissionControl-Teams-Token": "token-1"})
        with patch.dict("os.environ", {"TEAMS_VERIFICATION_TOKEN": "token-1"}, clear=False):
            first = asyncio.run(teams_events(req))
            second = asyncio.run(teams_events(req))
        self.assertTrue(first.get("ok"))
        self.assertTrue(second.get("duplicate"))

    def test_teams_event_requires_binding(self):
        payload = {
            "type": "message",
            "mission_id": "mission-a",
            "channel_id": "19:other@thread.tacv2",
        }
        body = json.dumps(payload).encode("utf-8")
        req = _Req(body, headers={"X-MissionControl-Teams-Token": "token-1"})
        with patch.dict("os.environ", {"TEAMS_VERIFICATION_TOKEN": "token-1"}, clear=False):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(teams_events(req))
        self.assertEqual(ctx.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
