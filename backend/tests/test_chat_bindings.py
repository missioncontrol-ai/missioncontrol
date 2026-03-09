import unittest
from types import SimpleNamespace

from fastapi import HTTPException
from sqlmodel import SQLModel

from app.db import engine, get_session
from app.models import Mission, MissionRoleMembership
from app.routers.chat_integrations import (
    create_chat_binding,
    delete_chat_binding,
    list_chat_bindings,
)
from app.schemas import SlackChannelBindingCreate


def _request(email: str, subject: str = "oidc-subject"):
    return SimpleNamespace(state=SimpleNamespace(principal={"email": email, "subject": subject}))


class ChatBindingTests(unittest.TestCase):
    def setUp(self):
        SQLModel.metadata.drop_all(engine)
        SQLModel.metadata.create_all(engine)
        with get_session() as session:
            mission = Mission(id="mission-a", name="mission-a", owners="owner@example.com")
            session.add(mission)
            session.add(
                MissionRoleMembership(
                    mission_id="mission-a",
                    subject="contrib@example.com",
                    role="mission_contributor",
                )
            )
            session.commit()

    def test_owner_can_create_list_and_delete_provider_binding(self):
        owner = _request("owner@example.com")
        created = create_chat_binding(
            SlackChannelBindingCreate(
                provider="teams",
                mission_id="mission-a",
                workspace_external_id="tenant-1",
                channel_id="19:abc123@thread.tacv2",
                channel_name="ops-channel",
                channel_metadata={"webhook_url": "https://outlook.office.com/webhook/..."},
            ),
            owner,
        )
        self.assertEqual(created["provider"], "teams")
        listed = list_chat_bindings("mission-a", owner, provider="teams")
        self.assertEqual(len(listed), 1)
        deleted = delete_chat_binding(created["id"], owner)
        self.assertTrue(deleted["ok"])

    def test_unknown_provider_is_rejected(self):
        owner = _request("owner@example.com")
        with self.assertRaises(HTTPException) as ctx:
            create_chat_binding(
                SlackChannelBindingCreate(
                    provider="discord",
                    mission_id="mission-a",
                    channel_id="123",
                ),
                owner,
            )
        self.assertEqual(ctx.exception.status_code, 400)

    def test_contributor_cannot_create_binding(self):
        contributor = _request("contrib@example.com")
        with self.assertRaises(HTTPException) as ctx:
            create_chat_binding(
                SlackChannelBindingCreate(provider="slack", mission_id="mission-a", channel_id="C999"),
                contributor,
            )
        self.assertEqual(ctx.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
