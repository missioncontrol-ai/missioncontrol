import unittest
from types import SimpleNamespace

from fastapi import HTTPException
from sqlmodel import SQLModel

from app.db import engine, get_session
from app.models import Mission
from app.routers.klusters import create_kluster, update_kluster
from app.routers.missions import create_mission, update_mission
from app.schemas import KlusterCreate, MissionCreate, MissionUpdate


def _request(email: str | None = None, subject: str = "oidc-subject"):
    return SimpleNamespace(
        state=SimpleNamespace(principal={"email": email, "subject": subject}),
        headers={},
        url=SimpleNamespace(path="/tests"),
    )


class OwnerRequirementsTests(unittest.TestCase):
    def setUp(self):
        SQLModel.metadata.drop_all(engine)
        SQLModel.metadata.create_all(engine)

    def test_create_mission_requires_at_least_one_owner(self):
        with self.assertRaises(HTTPException) as ctx:
            create_mission(MissionCreate(name="m-no-owner"), _request(email=None, subject="token-client"))
        self.assertEqual(ctx.exception.status_code, 422)
        self.assertIn("owners must include at least one owner", str(ctx.exception.detail))

    def test_create_mission_autofills_owner_from_actor(self):
        mission = create_mission(MissionCreate(name="m-autofill"), _request(email="owner@example.com"))
        self.assertEqual(mission.owners, "owner@example.com")

    def test_update_mission_rejects_empty_owner_list(self):
        with get_session() as session:
            session.add(Mission(id="mission-a", name="mission-a", owners="owner@example.com"))
            session.commit()
        with self.assertRaises(HTTPException) as ctx:
            update_mission("mission-a", MissionUpdate(owners=" ,  "), _request(email="owner@example.com"))
        self.assertEqual(ctx.exception.status_code, 422)
        self.assertIn("owners must include at least one owner", str(ctx.exception.detail))

    def test_create_and_update_kluster_require_owner(self):
        mission = create_mission(MissionCreate(name="mission-a", owners="owner@example.com"), _request(email="owner@example.com"))
        with self.assertRaises(HTTPException) as create_ctx:
            create_kluster(
                mission.id,
                KlusterCreate(name="cluster-a", owners="   "),
                _request(email="owner@example.com"),
            )
        self.assertEqual(create_ctx.exception.status_code, 422)

        created = create_kluster(
            mission.id,
            KlusterCreate(name="cluster-a", owners="owner@example.com"),
            _request(email="owner@example.com"),
        )
        with self.assertRaises(HTTPException) as update_ctx:
            update_kluster(
                mission.id,
                created.id,
                KlusterCreate(name="cluster-a", owners=""),
                _request(email="owner@example.com"),
            )
        self.assertEqual(update_ctx.exception.status_code, 422)


if __name__ == "__main__":
    unittest.main()
