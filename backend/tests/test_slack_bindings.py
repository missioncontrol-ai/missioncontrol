import unittest
from types import SimpleNamespace

from fastapi import HTTPException
from sqlmodel import SQLModel

from app.db import engine, get_session
from app.models import Mission, MissionRoleMembership
from app.routers.slack_integrations import create_binding, delete_binding, list_bindings
from app.schemas import SlackChannelBindingCreate


def _request(email: str, subject: str = "oidc-subject"):
    return SimpleNamespace(state=SimpleNamespace(principal={"email": email, "subject": subject}))


class SlackBindingTests(unittest.TestCase):
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

    def test_owner_can_create_list_and_delete_binding(self):
        owner = _request("owner@example.com")
        created = create_binding(
            SlackChannelBindingCreate(
                provider="slack",
                mission_id="mission-a",
                workspace_external_id="T123",
                channel_id="C123",
                channel_name="eng-ai",
                channel_metadata={"team_name": "core-eng"},
            ),
            owner,
        )
        self.assertEqual(created["channel_id"], "C123")
        self.assertEqual(created["provider"], "slack")
        self.assertEqual(created["workspace_external_id"], "T123")
        self.assertEqual(created["channel_metadata"].get("team_name"), "core-eng")
        listed = list_bindings("mission-a", owner)
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["provider"], "slack")
        deleted = delete_binding(created["id"], owner)
        self.assertTrue(deleted["ok"])

    def test_contributor_cannot_create_binding(self):
        contributor = _request("contrib@example.com")
        with self.assertRaises(HTTPException) as ctx:
            create_binding(
                SlackChannelBindingCreate(mission_id="mission-a", channel_id="C999", channel_name="private"),
                contributor,
            )
        self.assertEqual(ctx.exception.status_code, 403)

    def test_unknown_provider_is_rejected(self):
        owner = _request("owner@example.com")
        with self.assertRaises(HTTPException) as ctx:
            create_binding(
                SlackChannelBindingCreate(
                    provider="discord",
                    mission_id="mission-a",
                    channel_id="C123",
                ),
                owner,
            )
        self.assertEqual(ctx.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
