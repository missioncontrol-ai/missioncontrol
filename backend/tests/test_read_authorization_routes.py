import unittest
from types import SimpleNamespace

from fastapi import HTTPException
from sqlmodel import SQLModel

from app.db import engine, get_session
from app.models import Artifact, Doc, Kluster, Mission, MissionRoleMembership, Task
from app.routers.artifacts import list_artifacts
from app.routers.docs import list_docs
from app.routers.explorer import get_explorer_node, get_explorer_tree
from app.routers.klusters import list_klusters
from app.routers.tasks import list_tasks


def _request(email: str, subject: str = "oidc-subject"):
    return SimpleNamespace(state=SimpleNamespace(principal={"email": email, "subject": subject}))


class ReadAuthorizationRouteTests(unittest.TestCase):
    def setUp(self):
        SQLModel.metadata.drop_all(engine)
        SQLModel.metadata.create_all(engine)
        with get_session() as session:
            session.add(Mission(id="mission-a", name="mission-a", owners="owner-a@example.com"))
            session.add(Mission(id="mission-b", name="mission-b", owners="owner-b@example.com", visibility="private"))
            session.add(
                Kluster(
                    id="kluster-a",
                    mission_id="mission-a",
                    name="kluster-a",
                    owners="owner-a@example.com",
                )
            )
            session.add(
                Kluster(
                    id="kluster-b",
                    mission_id="mission-b",
                    name="kluster-b",
                    owners="owner-b@example.com",
                )
            )
            session.add(MissionRoleMembership(mission_id="mission-a", subject="viewer@example.com", role="mission_viewer"))
            session.add(Task(kluster_id="kluster-a", title="task-a"))
            session.add(Task(kluster_id="kluster-b", title="task-b"))
            session.add(Doc(kluster_id="kluster-a", title="doc-a", body="a"))
            session.add(Doc(kluster_id="kluster-b", title="doc-b", body="b"))
            session.add(Artifact(kluster_id="kluster-a", name="artifact-a", uri="file://a", artifact_type="file"))
            session.add(Artifact(kluster_id="kluster-b", name="artifact-b", uri="file://b", artifact_type="file"))
            session.commit()

        self.viewer_req = _request(email="viewer@example.com")

    def test_kluster_and_task_reads_forbid_cross_mission_access(self):
        with self.assertRaises(HTTPException) as kluster_ctx:
            list_klusters("mission-b", self.viewer_req)
        self.assertEqual(kluster_ctx.exception.status_code, 403)

        with self.assertRaises(HTTPException) as task_ctx:
            list_tasks("mission-b", "kluster-b", request=self.viewer_req)
        self.assertEqual(task_ctx.exception.status_code, 403)

    def test_docs_and_artifacts_lists_are_mission_scoped(self):
        docs = list_docs(request=self.viewer_req)
        artifacts = list_artifacts(request=self.viewer_req)
        self.assertEqual([doc.title for doc in docs], ["doc-a"])
        self.assertEqual([artifact.name for artifact in artifacts], ["artifact-a"])

    def test_explorer_tree_and_node_are_mission_scoped(self):
        tree = get_explorer_tree(limit_tasks_per_cluster=5, request=self.viewer_req)
        self.assertEqual([mission.id for mission in tree.missions], ["mission-a"])

        with self.assertRaises(HTTPException) as mission_ctx:
            get_explorer_node("mission", "mission-b", limit_tasks=50, request=self.viewer_req)
        self.assertEqual(mission_ctx.exception.status_code, 403)

        with self.assertRaises(HTTPException) as kluster_ctx:
            get_explorer_node("kluster", "kluster-b", limit_tasks=50, request=self.viewer_req)
        self.assertEqual(kluster_ctx.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
