import unittest
from types import SimpleNamespace

from sqlmodel import SQLModel

from app.db import engine, get_session
from app.models import Doc, Kluster, Mission, MissionRoleMembership, Task
import app.routers.search as search_router


def _request(email: str, subject: str = "oidc-subject"):
    return SimpleNamespace(state=SimpleNamespace(principal={"email": email, "subject": subject}))


class ReadIsolationTests(unittest.TestCase):
    def setUp(self):
        SQLModel.metadata.drop_all(engine)
        SQLModel.metadata.create_all(engine)
        self._orig_query_tasks = search_router.query_tasks
        self._orig_query_docs = search_router.query_docs

        with get_session() as session:
            mission_a = Mission(id="mission-a", name="mission-a", owners="owner-a@example.com")
            mission_b = Mission(id="mission-b", name="mission-b", owners="owner-b@example.com")
            session.add(mission_a)
            session.add(mission_b)

            kluster_a = Kluster(
                id="kluster-a",
                mission_id="mission-a",
                name="kluster-a",
                owners="owner-a@example.com",
            )
            kluster_b = Kluster(
                id="kluster-b",
                mission_id="mission-b",
                name="kluster-b",
                owners="owner-b@example.com",
            )
            session.add(kluster_a)
            session.add(kluster_b)

            task_a = Task(kluster_id="kluster-a", title="task-a")
            task_b = Task(kluster_id="kluster-b", title="task-b")
            session.add(task_a)
            session.add(task_b)

            doc_a = Doc(kluster_id="kluster-a", title="doc-a", body="body-a")
            doc_b = Doc(kluster_id="kluster-b", title="doc-b", body="body-b")
            session.add(doc_a)
            session.add(doc_b)

            session.add(
                MissionRoleMembership(
                    mission_id="mission-a",
                    subject="viewer@example.com",
                    role="mission_viewer",
                )
            )
            session.commit()
            session.refresh(task_a)
            session.refresh(task_b)
            session.refresh(doc_a)
            session.refresh(doc_b)
            self.task_a_id = task_a.id
            self.task_b_id = task_b.id
            self.doc_a_id = doc_a.id
            self.doc_b_id = doc_b.id

    def tearDown(self):
        search_router.query_tasks = self._orig_query_tasks
        search_router.query_docs = self._orig_query_docs

    def test_search_is_mission_scoped_for_viewer(self):
        search_router.query_tasks = lambda q, limit=5: [
            {"id": self.task_a_id, "distance": 0.1},
            {"id": self.task_b_id, "distance": 0.2},
        ]
        search_router.query_docs = lambda q, limit=5: [
            {"id": self.doc_a_id, "distance": 0.1},
            {"id": self.doc_b_id, "distance": 0.2},
        ]

        viewer_req = _request(email="viewer@example.com")
        klusters = search_router.search_klusters("kluster", limit=10, request=viewer_req)
        tasks = search_router.search_tasks("task", limit=10, request=viewer_req)
        docs = search_router.search_docs("doc", limit=10, request=viewer_req)

        self.assertEqual([item["id"] for item in klusters["results"]], ["kluster-a"])
        self.assertEqual([item["id"] for item in tasks["results"]], [self.task_a_id])
        self.assertEqual([item["id"] for item in docs["results"]], [self.doc_a_id])

    def test_search_returns_empty_for_unscoped_principal(self):
        search_router.query_tasks = lambda q, limit=5: [{"id": self.task_a_id, "distance": 0.1}]
        search_router.query_docs = lambda q, limit=5: [{"id": self.doc_a_id, "distance": 0.1}]

        outsider_req = _request(email="outsider@example.com")
        klusters = search_router.search_klusters("kluster", limit=10, request=outsider_req)
        tasks = search_router.search_tasks("task", limit=10, request=outsider_req)
        docs = search_router.search_docs("doc", limit=10, request=outsider_req)

        self.assertEqual(klusters["results"], [])
        self.assertEqual(tasks["results"], [])
        self.assertEqual(docs["results"], [])


if __name__ == "__main__":
    unittest.main()
