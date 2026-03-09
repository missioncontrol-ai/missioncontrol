import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from sqlmodel import SQLModel

from app.db import engine, get_session
from app.models import Mission, SlackChannelBinding
from app.routers.google_chat_integrations import google_chat_events


class _Req:
    def __init__(self, body: bytes, headers: dict | None = None):
        self._body = body
        self.headers = headers or {}
        self.app = SimpleNamespace(state=SimpleNamespace(mqtt=None))

    async def body(self):
        return self._body


class GoogleChatIntegrationTests(unittest.TestCase):
    def setUp(self):
        SQLModel.metadata.drop_all(engine)
        SQLModel.metadata.create_all(engine)
        with get_session() as session:
            session.add(Mission(id="mission-a", name="mission-a", owners="owner@example.com"))
            session.add(
                SlackChannelBinding(
                    provider="google_chat",
                    mission_id="mission-a",
                    channel_id="spaces/AAA111",
                    channel_name="ops-space",
                    channel_metadata_json=json.dumps({"webhook_url": "https://chat.googleapis.com/v1/spaces/..."}),
                )
            )
            session.commit()

    def test_google_chat_event_dedupes(self):
        payload = {
            "type": "MESSAGE",
            "mission_id": "mission-a",
            "space": {"name": "spaces/AAA111"},
        }
        body = json.dumps(payload).encode("utf-8")
        req = _Req(body, headers={"X-Goog-Chat-Token": "token-1"})
        with patch.dict("os.environ", {"GOOGLE_CHAT_VERIFICATION_TOKEN": "token-1"}, clear=False):
            first = asyncio.run(google_chat_events(req))
            second = asyncio.run(google_chat_events(req))
        self.assertTrue(first.get("ok"))
        self.assertTrue(second.get("duplicate"))

    def test_google_chat_event_requires_binding(self):
        payload = {
            "type": "MESSAGE",
            "mission_id": "mission-a",
            "space": {"name": "spaces/OTHER"},
        }
        body = json.dumps(payload).encode("utf-8")
        req = _Req(body, headers={"X-Goog-Chat-Token": "token-1"})
        with patch.dict("os.environ", {"GOOGLE_CHAT_VERIFICATION_TOKEN": "token-1"}, clear=False):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(google_chat_events(req))
        self.assertEqual(ctx.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
