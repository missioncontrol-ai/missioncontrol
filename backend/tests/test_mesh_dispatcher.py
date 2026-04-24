"""Tests for MeshDispatcher (app/services/mesh_dispatcher.py)."""
import json
import unittest
import uuid
from datetime import datetime
from unittest.mock import patch

from sqlmodel import Session, SQLModel, create_engine

from app.models import Kluster, MeshTask


def _make_engine():
    eng = create_engine("sqlite://")
    SQLModel.metadata.drop_all(eng, checkfirst=True)
    SQLModel.metadata.create_all(eng)
    return eng


def _add_task(
    session: Session,
    kluster_id: str,
    status: str,
    depends_on: list | None = None,
    mission_id: str = "m1",
) -> str:
    tid = str(uuid.uuid4())
    session.add(MeshTask(
        id=tid,
        kluster_id=kluster_id,
        mission_id=mission_id,
        title="test",
        status=status,
        depends_on=json.dumps(depends_on or []),
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    ))
    session.commit()
    return tid


class TestMeshDispatcher(unittest.TestCase):
    def setUp(self):
        self.engine = _make_engine()
        self.kluster_id = str(uuid.uuid4())
        with Session(self.engine) as s:
            s.add(Kluster(
                id=self.kluster_id,
                mission_id="m1",
                name="k1",
                owners="agent@example.com",
            ))
            s.commit()

    def tearDown(self):
        self.engine.dispose()

    def _get_task(self, task_id: str) -> MeshTask:
        with Session(self.engine) as s:
            return s.get(MeshTask, task_id)

    def _tick(self) -> int:
        from app.services import mesh_dispatcher
        original_engine = mesh_dispatcher.engine
        mesh_dispatcher.engine = self.engine
        try:
            with patch("app.services.mesh_events._notify_postgres"):
                return mesh_dispatcher.tick()
        finally:
            mesh_dispatcher.engine = original_engine

    def test_blocked_task_with_finished_dep_is_promoted(self):
        with Session(self.engine) as s:
            dep_id = _add_task(s, self.kluster_id, "finished")
            child_id = _add_task(s, self.kluster_id, "blocked", depends_on=[dep_id])

        promoted = self._tick()
        self.assertEqual(promoted, 1)

        child = self._get_task(child_id)
        self.assertEqual(child.status, "ready")

    def test_pending_task_with_finished_dep_is_promoted(self):
        with Session(self.engine) as s:
            dep_id = _add_task(s, self.kluster_id, "finished")
            child_id = _add_task(s, self.kluster_id, "pending", depends_on=[dep_id])

        promoted = self._tick()
        self.assertEqual(promoted, 1)
        self.assertEqual(self._get_task(child_id).status, "ready")

    def test_blocked_task_with_unfinished_dep_is_not_promoted(self):
        with Session(self.engine) as s:
            dep_id = _add_task(s, self.kluster_id, "running")
            child_id = _add_task(s, self.kluster_id, "blocked", depends_on=[dep_id])

        promoted = self._tick()
        self.assertEqual(promoted, 0)
        self.assertEqual(self._get_task(child_id).status, "blocked")

    def test_task_ready_event_is_emitted_for_promoted_task(self):
        with Session(self.engine) as s:
            dep_id = _add_task(s, self.kluster_id, "finished")
            child_id = _add_task(s, self.kluster_id, "blocked", depends_on=[dep_id])

        emitted: list[tuple] = []

        def capture(event_type, task_id, *args, **kwargs):
            emitted.append((event_type, task_id))

        with patch("app.services.mesh_events.publish_task_event", side_effect=capture):
            self._tick()

        ready_events = [(t, tid) for t, tid in emitted if t == "task_ready"]
        self.assertEqual(len(ready_events), 1)
        self.assertEqual(ready_events[0][1], child_id)

    def test_already_ready_task_is_not_double_promoted(self):
        with Session(self.engine) as s:
            dep_id = _add_task(s, self.kluster_id, "finished")
            child_id = _add_task(s, self.kluster_id, "ready", depends_on=[dep_id])

        promoted = self._tick()
        self.assertEqual(promoted, 0)

    def test_idempotent_on_second_tick(self):
        with Session(self.engine) as s:
            dep_id = _add_task(s, self.kluster_id, "finished")
            _add_task(s, self.kluster_id, "blocked", depends_on=[dep_id])

        with patch("app.services.mesh_events._notify_postgres"):
            self._tick()
            promoted_second = self._tick()

        self.assertEqual(promoted_second, 0)


if __name__ == "__main__":
    unittest.main()
