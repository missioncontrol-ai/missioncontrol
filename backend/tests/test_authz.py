import unittest
from types import SimpleNamespace

from fastapi import HTTPException
from sqlmodel import Session, SQLModel, create_engine

from app.models import Mission, MissionRoleMembership
from app.services.authz import (
    ROLE_OWNER,
    assert_mission_owner_or_admin,
    is_platform_admin,
    upsert_mission_role,
)


def _request(email: str | None = None, subject: str | None = None):
    principal = {"email": email, "subject": subject}
    return SimpleNamespace(state=SimpleNamespace(principal=principal))


class AuthzTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        SQLModel.metadata.create_all(self.engine)

    def test_service_token_is_admin(self):
        req = _request(subject="service-token")
        self.assertTrue(is_platform_admin(req))

    def test_legacy_owner_can_manage_mission(self):
        with Session(self.engine) as session:
            mission = Mission(id="abc123def456", name="m1", owners="owner@example.com")
            session.add(mission)
            session.commit()
            req = _request(email="owner@example.com", subject="oidc-subject")
            resolved = assert_mission_owner_or_admin(session=session, request=req, mission_id=mission.id)
            self.assertEqual(resolved.id, mission.id)

    def test_role_owner_can_manage_mission(self):
        with Session(self.engine) as session:
            mission = Mission(id="abc123def456", name="m1", owners="")
            session.add(mission)
            session.commit()
            upsert_mission_role(
                session=session,
                mission_id=mission.id,
                subject="owner@example.com",
                role=ROLE_OWNER,
            )
            session.commit()
            req = _request(email="owner@example.com", subject="oidc-subject")
            resolved = assert_mission_owner_or_admin(session=session, request=req, mission_id=mission.id)
            self.assertEqual(resolved.id, mission.id)

    def test_non_owner_is_forbidden(self):
        with Session(self.engine) as session:
            mission = Mission(id="abc123def456", name="m1", owners="owner@example.com")
            session.add(mission)
            session.commit()
            req = _request(email="viewer@example.com", subject="oidc-subject")
            with self.assertRaises(HTTPException) as ctx:
                assert_mission_owner_or_admin(session=session, request=req, mission_id=mission.id)
            self.assertEqual(ctx.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
