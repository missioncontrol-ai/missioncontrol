import unittest
from types import SimpleNamespace

from fastapi import HTTPException
from sqlmodel import SQLModel, select

from app.db import engine, get_session
from app.models import Artifact, Doc, Kluster, LedgerEvent, Mission, MissionRoleMembership
from app.routers.artifacts import delete_artifact
from app.routers.docs import delete_doc


def _request(email: str, subject: str = "oidc-subject"):
    return SimpleNamespace(
        state=SimpleNamespace(principal={"email": email, "subject": subject}),
        headers={},
        url=SimpleNamespace(path="/docs"),
    )


class DeleteAuthorizationRouteTests(unittest.TestCase):
    def setUp(self):
        SQLModel.metadata.drop_all(engine)
        SQLModel.metadata.create_all(engine)
        with get_session() as session:
            session.add(Mission(id="mission-a", name="mission-a", owners="owner@example.com"))
            session.add(Kluster(id="kluster-a", mission_id="mission-a", name="kluster-a", owners="owner@example.com"))
            session.add(MissionRoleMembership(mission_id="mission-a", subject="contrib@example.com", role="mission_contributor"))
            session.add(MissionRoleMembership(mission_id="mission-a", subject="viewer@example.com", role="mission_viewer"))
            session.add(Doc(id=1, kluster_id="kluster-a", title="doc-a", body="body"))
            session.add(Artifact(id=1, kluster_id="kluster-a", name="artifact-a", artifact_type="file", uri="file://a"))
            session.commit()

    def test_owner_can_delete_doc_and_event_is_audited(self):
        req = _request("owner@example.com")
        out = delete_doc(1, req)
        self.assertTrue(out["ok"])
        with get_session() as session:
            self.assertIsNone(session.get(Doc, 1))
            ev = session.exec(
                select(LedgerEvent)
                .where(LedgerEvent.entity_type == "doc")
                .where(LedgerEvent.entity_id == "1")
                .where(LedgerEvent.action == "delete")
            ).first()
            self.assertIsNotNone(ev)

    def test_contributor_can_delete_artifact(self):
        req = _request("contrib@example.com")
        out = delete_artifact(1, req)
        self.assertTrue(out["ok"])
        with get_session() as session:
            self.assertIsNone(session.get(Artifact, 1))

    def test_viewer_cannot_delete_doc_or_artifact(self):
        req = _request("viewer@example.com")
        with self.assertRaises(HTTPException) as doc_ctx:
            delete_doc(1, req)
        self.assertEqual(doc_ctx.exception.status_code, 403)
        with self.assertRaises(HTTPException) as artifact_ctx:
            delete_artifact(1, req)
        self.assertEqual(artifact_ctx.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
