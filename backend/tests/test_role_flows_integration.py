import unittest
from types import SimpleNamespace

from fastapi import HTTPException
from sqlmodel import SQLModel

from app.db import engine, get_session
from app.models import Task
from app.routers.klusters import create_cluster, delete_cluster, update_cluster
from app.routers.docs import create_doc, update_doc
from app.routers.artifacts import create_artifact, update_artifact
from app.routers.missions import (
    add_or_update_mission_role,
    create_mission,
    delete_mission,
    update_mission,
)
from app.routers.tasks import delete_task, update_task
import app.routers.tasks as tasks_router
from app.schemas import (
    ArtifactCreate,
    ArtifactUpdate,
    KlusterCreate,
    DocCreate,
    DocUpdate,
    MissionCreate,
    MissionRoleUpsert,
    MissionUpdate,
    TaskUpdate,
)
from app.services.schema_pack import DEFAULT_SCHEMA_PACK
import app.routers.docs as docs_router


def _request(*, email: str, subject: str = "oidc-subject"):
    return SimpleNamespace(
        state=SimpleNamespace(principal={"email": email, "subject": subject}),
        url=SimpleNamespace(path="/tests"),
        app=SimpleNamespace(state=SimpleNamespace(schema_pack=DEFAULT_SCHEMA_PACK)),
    )


class RoleFlowIntegrationTests(unittest.TestCase):
    def setUp(self):
        SQLModel.metadata.drop_all(engine)
        SQLModel.metadata.create_all(engine)
        tasks_router.index_task = lambda *args, **kwargs: None
        docs_router.index_doc = lambda *args, **kwargs: None

        self.owner_req = _request(email="owner@example.com")
        self.contrib_req = _request(email="contrib@example.com")
        self.viewer_req = _request(email="viewer@example.com")

    def _create_mission_with_contributor(self):
        mission = create_mission(
            MissionCreate(name="mission-a", owners="owner@example.com"),
            self.owner_req,
        )
        add_or_update_mission_role(
            mission.id,
            MissionRoleUpsert(subject="contrib@example.com", role="mission_contributor"),
            self.owner_req,
        )
        return mission

    def test_mission_contributor_can_update_but_cannot_delete(self):
        mission = self._create_mission_with_contributor()
        updated = update_mission(
            mission.id,
            MissionUpdate(description="new description"),
            self.contrib_req,
        )
        self.assertEqual(updated.description, "new description")

        with self.assertRaises(HTTPException) as ctx:
            delete_mission(mission.id, self.contrib_req)
        self.assertEqual(ctx.exception.status_code, 403)

    def test_cluster_contributor_can_update_but_cannot_delete(self):
        mission = self._create_mission_with_contributor()
        kluster = create_cluster(
            KlusterCreate(mission_id=mission.id, name="kluster-a"),
            self.owner_req,
        )

        updated = update_cluster(
            kluster.id,
            KlusterCreate(mission_id=mission.id, name="kluster-b"),
            self.contrib_req,
        )
        self.assertEqual(updated.name, "kluster-b")

        with self.assertRaises(HTTPException) as ctx:
            delete_cluster(kluster.id, self.contrib_req)
        self.assertEqual(ctx.exception.status_code, 403)

    def test_task_contributor_can_update_but_cannot_delete(self):
        mission = self._create_mission_with_contributor()
        kluster = create_cluster(
            KlusterCreate(mission_id=mission.id, name="kluster-a"),
            self.owner_req,
        )
        with get_session() as session:
            task = Task(kluster_id=kluster.id, title="task-a")
            session.add(task)
            session.commit()
            session.refresh(task)
            task_id = task.id

        updated = update_task(mission.id, kluster.id, task_id, TaskUpdate(title="task-b"), self.contrib_req)
        self.assertEqual(updated.title, "task-b")

        with self.assertRaises(HTTPException) as ctx:
            delete_task(mission.id, kluster.id, task_id, self.contrib_req)
        self.assertEqual(ctx.exception.status_code, 403)

    def test_owner_can_delete_after_contributor_denied(self):
        mission = self._create_mission_with_contributor()
        kluster = create_cluster(
            KlusterCreate(mission_id=mission.id, name="kluster-a"),
            self.owner_req,
        )

        with self.assertRaises(HTTPException):
            delete_cluster(kluster.id, self.contrib_req)
        deleted_cluster = delete_cluster(kluster.id, self.owner_req)
        self.assertTrue(deleted_cluster["ok"])

        deleted_mission = delete_mission(mission.id, self.owner_req)
        self.assertTrue(deleted_mission["ok"])

    def test_doc_contributor_can_update_but_viewer_cannot(self):
        mission = self._create_mission_with_contributor()
        kluster = create_cluster(
            KlusterCreate(mission_id=mission.id, name="kluster-a"),
            self.owner_req,
        )
        doc = create_doc(
            DocCreate(kluster_id=kluster.id, title="doc-a", body="body"),
            self.owner_req,
        )
        updated = update_doc(doc.id, DocUpdate(title="doc-b"), self.contrib_req)
        self.assertEqual(updated.title, "doc-b")
        with self.assertRaises(HTTPException) as ctx:
            update_doc(doc.id, DocUpdate(title="doc-c"), self.viewer_req)
        self.assertEqual(ctx.exception.status_code, 403)

    def test_artifact_contributor_can_update_but_viewer_cannot(self):
        mission = self._create_mission_with_contributor()
        kluster = create_cluster(
            KlusterCreate(mission_id=mission.id, name="kluster-a"),
            self.owner_req,
        )
        artifact = create_artifact(
            ArtifactCreate(kluster_id=kluster.id, name="artifact-a", artifact_type="spec", uri="file://spec.md"),
            self.owner_req,
        )
        updated = update_artifact(
            artifact.id,
            ArtifactUpdate(name="artifact-b"),
            self.contrib_req,
        )
        self.assertEqual(updated.name, "artifact-b")
        with self.assertRaises(HTTPException) as ctx:
            update_artifact(artifact.id, ArtifactUpdate(name="artifact-c"), self.viewer_req)
        self.assertEqual(ctx.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
