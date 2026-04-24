"""Tests for the task_ready event and blocked → ready status transitions."""
import asyncio
import unittest
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import call, patch

from sqlmodel import Session, SQLModel, create_engine, select

import app.routers.work as work
from app.models import Kluster, MeshTask
from app.services.work_watchdog import _tick

SUBJECT = "agent@example.com"


def _req(subject: str = SUBJECT):
    return SimpleNamespace(
        state=SimpleNamespace(principal={"subject": subject, "email": subject})
    )


class TaskReadyTestCase(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        SQLModel.metadata.drop_all(self.engine, checkfirst=True)
        SQLModel.metadata.create_all(self.engine)
        self._patches = [
            patch.object(work, "get_session", self._session_scope()),
            patch.object(work, "actor_subject_from_request", lambda _: SUBJECT),
        ]
        for p in self._patches:
            p.start()

        # Seed a kluster
        self.kluster_id = str(uuid.uuid4())
        with Session(self.engine) as s:
            s.add(Kluster(
                id=self.kluster_id,
                mission_id="mission-1",
                name="k1",
                owners=SUBJECT,
            ))
            s.commit()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self.engine.dispose()

    def _session_scope(self):
        engine = self.engine

        @contextmanager
        def _ctx():
            with Session(engine) as session:
                yield session

        return _ctx

    def _get_task(self, task_id: str) -> MeshTask:
        with Session(self.engine) as s:
            return s.get(MeshTask, task_id)

    # ------------------------------------------------------------------
    # Test: tasks with deps start as blocked, not pending
    # ------------------------------------------------------------------

    def test_task_with_deps_starts_blocked(self):
        a = work.create_task(
            self.kluster_id,
            work.MeshTaskCreate(title="A"),
            _req(),
        )
        self.assertEqual(a["status"], "ready")

        with patch("app.services.mesh_events._notify_postgres"):
            b = work.create_task(
                self.kluster_id,
                work.MeshTaskCreate(title="B", depends_on=[a["id"]]),
                _req(),
            )
        self.assertEqual(b["status"], "blocked")

    # ------------------------------------------------------------------
    # Test: completing A unblocks B → ready and emits task_ready for B
    # ------------------------------------------------------------------

    def test_complete_task_unblocks_dependent_and_emits_task_ready(self):
        with patch("app.services.mesh_events._notify_postgres"):
            a = work.create_task(
                self.kluster_id,
                work.MeshTaskCreate(title="A"),
                _req(),
            )
            b = work.create_task(
                self.kluster_id,
                work.MeshTaskCreate(title="B", depends_on=[a["id"]]),
                _req(),
            )

        self.assertEqual(b["status"], "blocked")

        # Claim A so we can complete it
        with patch("app.services.mesh_events._notify_postgres"):
            work.claim_task(a["id"], _req())

        emitted: list[tuple] = []

        def capture(event_type, task_id, *args, **kwargs):
            emitted.append((event_type, task_id))

        with patch("app.services.mesh_events._notify_postgres"):
            with patch("app.services.mesh_events.publish_task_event", side_effect=capture):
                work.complete_task(a["id"])

        b_task = self._get_task(b["id"])
        self.assertEqual(b_task.status, "ready")

        event_types = [e[0] for e in emitted]
        self.assertIn("task_completed", event_types)
        self.assertIn("task_ready", event_types)

        # task_ready must be for B
        ready_events = [(t, tid) for t, tid in emitted if t == "task_ready"]
        self.assertEqual(len(ready_events), 1)
        self.assertEqual(ready_events[0][1], b["id"])

    # ------------------------------------------------------------------
    # Test: A → B → C chain: completing A unblocks B but not C
    # ------------------------------------------------------------------

    def test_chain_only_unblocks_direct_dependent(self):
        with patch("app.services.mesh_events._notify_postgres"):
            a = work.create_task(self.kluster_id, work.MeshTaskCreate(title="A"), _req())
            b = work.create_task(
                self.kluster_id,
                work.MeshTaskCreate(title="B", depends_on=[a["id"]]),
                _req(),
            )
            c = work.create_task(
                self.kluster_id,
                work.MeshTaskCreate(title="C", depends_on=[b["id"]]),
                _req(),
            )

        with patch("app.services.mesh_events._notify_postgres"):
            work.claim_task(a["id"], _req())

        with patch("app.services.mesh_events._notify_postgres"):
            work.complete_task(a["id"])

        b_task = self._get_task(b["id"])
        c_task = self._get_task(c["id"])
        self.assertEqual(b_task.status, "ready")
        self.assertEqual(c_task.status, "blocked")

    # ------------------------------------------------------------------
    # Test: lease expiry re-readies a claimed task and emits task_ready
    # ------------------------------------------------------------------

    def test_lease_expiry_emits_task_ready(self):
        with patch("app.services.mesh_events._notify_postgres"):
            a = work.create_task(self.kluster_id, work.MeshTaskCreate(title="A"), _req())
            work.claim_task(a["id"], _req())

        # Force lease to be expired
        with Session(self.engine) as s:
            task = s.get(MeshTask, a["id"])
            task.lease_expires_at = datetime.utcnow() - timedelta(minutes=1)
            s.add(task)
            s.commit()

        emitted: list[tuple] = []

        def capture(event_type, task_id, *args, **kwargs):
            emitted.append((event_type, task_id))

        with patch("app.services.mesh_events._notify_postgres"):
            with patch("app.services.mesh_events.publish_task_event", side_effect=capture):
                with Session(self.engine) as session:
                    _tick(session)

        task = self._get_task(a["id"])
        self.assertEqual(task.status, "ready")

        emitted_types = [e[0] for e in emitted]
        self.assertIn("task_ready", emitted_types)
        ready_for_a = [(t, tid) for t, tid in emitted if t == "task_ready" and tid == a["id"]]
        self.assertEqual(len(ready_for_a), 1)


if __name__ == "__main__":
    unittest.main()
