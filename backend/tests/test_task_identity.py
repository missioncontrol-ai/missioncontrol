import unittest
from types import SimpleNamespace

from sqlmodel import SQLModel

from app.db import engine, get_session
from app.models import Kluster, Mission, Task
from app.services.schema_pack import DEFAULT_SCHEMA_PACK
from app.services.task_identity import ensure_task_public_id, resolve_task_by_ref


def _request(email: str):
    return SimpleNamespace(
        state=SimpleNamespace(principal={"email": email, "subject": email}),
        url=SimpleNamespace(path="/tests"),
        app=SimpleNamespace(state=SimpleNamespace(schema_pack=DEFAULT_SCHEMA_PACK)),
        headers={},
    )


class TaskIdentityTests(unittest.TestCase):
    def setUp(self):
        SQLModel.metadata.drop_all(engine)
        SQLModel.metadata.create_all(engine)
        with get_session() as session:
            mission = Mission(id="mission123abc", name="m1", owners="owner@example.com")
            kluster = Kluster(id="kluster123abc", mission_id=mission.id, name="k1", owners="owner@example.com")
            task = Task(kluster_id=kluster.id, title="task one")
            session.add(mission)
            session.add(kluster)
            session.add(task)
            session.commit()
            session.refresh(task)
            ensure_task_public_id(session, task)
            self.task_id = task.id
            self.task_public_id = task.public_id

    def test_public_id_generated_and_resolvable(self):
        self.assertEqual(len(self.task_public_id), 12)
        with get_session() as session:
            by_public = resolve_task_by_ref(session=session, task_ref=self.task_public_id)
            by_internal = resolve_task_by_ref(session=session, task_ref=str(self.task_id))
            self.assertIsNotNone(by_public)
            self.assertIsNotNone(by_internal)
            self.assertEqual(by_public.id, self.task_id)
            self.assertEqual(by_internal.id, self.task_id)


if __name__ == "__main__":
    unittest.main()
