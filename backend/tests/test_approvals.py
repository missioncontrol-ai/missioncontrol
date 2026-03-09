import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from sqlmodel import SQLModel

from app.db import engine, get_session
from app.models import Mission, MissionRoleMembership
from app.routers.approvals import approve_request, create_approval_request, list_approval_requests
from app.schemas import ApprovalRequestCreate, ApprovalRequestDecision


def _request(email: str, subject: str = "oidc-subject"):
    return SimpleNamespace(
        state=SimpleNamespace(principal={"email": email, "subject": subject}),
        headers={},
    )


class ApprovalFlowTests(unittest.TestCase):
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

    def test_create_list_and_approve_request(self):
        contributor = _request("contrib@example.com")
        owner = _request("owner@example.com")
        created = create_approval_request(
            ApprovalRequestCreate(
                mission_id="mission-a",
                action="task.update",
                reason="Need policy-gated update",
            ),
            contributor,
        )
        self.assertEqual(created["status"], "pending")
        listed = list_approval_requests(owner, mission_id="mission-a")
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["id"], created["id"])

        with patch.dict(os.environ, {"MC_APPROVAL_TOKEN_SECRET": "test-secret"}, clear=False):
            approved = approve_request(
                created["id"],
                ApprovalRequestDecision(note="approved"),
                owner,
            )
        self.assertTrue(isinstance(approved["approval_token"], str) and approved["approval_token"])
        self.assertEqual(approved["approval"]["status"], "approved")


if __name__ == "__main__":
    unittest.main()
