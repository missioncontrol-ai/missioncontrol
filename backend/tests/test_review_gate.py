"""Tests for review gate endpoints in app/routers/work.py."""

import json
import unittest
import uuid
from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from sqlmodel import Session, SQLModel, create_engine

import app.routers.work as work
from app.models import Kluster, MeshTask

SUBJECT = "agent@example.com"


def _req(subject: str = SUBJECT):
    return SimpleNamespace(
        state=SimpleNamespace(principal={"subject": subject, "email": subject})
    )


class ReviewGateTestCase(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        SQLModel.metadata.drop_all(self.engine, checkfirst=True)
        SQLModel.metadata.create_all(self.engine)
        self._patches = [
            patch.object(work, "get_session", self._session_scope()),
            patch.object(work, "actor_subject_from_request", lambda _req: SUBJECT),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()

    def _session_scope(self):
        engine = self.engine

        @contextmanager
        def _ctx():
            with Session(engine) as session:
                yield session

        return _ctx

    def _make_kluster(self, mission_id: str = "mission-1") -> str:
        kid = str(uuid.uuid4())
        with Session(self.engine) as s:
            s.add(Kluster(id=kid, mission_id=mission_id, name="test kluster", owners=SUBJECT))
            s.commit()
        return kid

    def _make_task(self, kluster_id: str, status: str = "claimed") -> str:
        tid = str(uuid.uuid4())
        with Session(self.engine) as s:
            s.add(
                MeshTask(
                    id=tid,
                    kluster_id=kluster_id,
                    mission_id="mission-1",
                    title="task",
                    description="",
                    input_json="{}",
                    claim_policy="first_claim",
                    depends_on="[]",
                    produces="{}",
                    consumes="{}",
                    required_capabilities="[]",
                    status=status,
                    priority=0,
                    created_by_subject=SUBJECT,
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                )
            )
            s.commit()
        return tid

    def _get_task(self, task_id: str) -> MeshTask:
        with Session(self.engine) as s:
            return s.get(MeshTask, task_id)

    def _create_gate(self, task_id: str, gate_type: str = "post_task") -> dict:
        body = work.GateCreate(gate_type=gate_type)
        return work.create_gate(task_id, body, _req())

    def _complete_task(self, task_id: str) -> dict:
        body = work.CompleteBody()
        return work.complete_task(task_id, body)

    def _resolve_gate(self, task_id: str, gate_id: str, decision: str) -> dict:
        body = work.GateResolve(decision=decision)
        return work.resolve_gate(task_id, gate_id, body, _req())

    # ------------------------------------------------------------------
    # Tests
    # ------------------------------------------------------------------

    def test_no_gates_completes_normally(self):
        kid = self._make_kluster()
        tid = self._make_task(kid, status="claimed")
        result = self._complete_task(tid)
        self.assertEqual(result["status"], "finished")
        task = self._get_task(tid)
        self.assertEqual(task.status, "finished")

    def test_complete_with_pending_gate_returns_waiting_review(self):
        kid = self._make_kluster()
        tid = self._make_task(kid, status="claimed")
        gate = self._create_gate(tid)
        self.assertEqual(gate["status"], "pending")

        result = self._complete_task(tid)
        self.assertEqual(result["status"], "waiting_review")
        self.assertIn(gate["id"], result["pending_gates"])

        task = self._get_task(tid)
        self.assertEqual(task.status, "waiting_review")

    def test_approve_gate_transitions_task_to_finished(self):
        kid = self._make_kluster()
        tid = self._make_task(kid, status="claimed")
        gate = self._create_gate(tid)
        self._complete_task(tid)  # puts task in waiting_review

        resolved = self._resolve_gate(tid, gate["id"], "approved")
        self.assertEqual(resolved["status"], "approved")

        task = self._get_task(tid)
        self.assertEqual(task.status, "finished")

    def test_reject_gate_transitions_task_to_failed(self):
        kid = self._make_kluster()
        tid = self._make_task(kid, status="claimed")
        gate = self._create_gate(tid)
        self._complete_task(tid)  # puts task in waiting_review

        resolved = self._resolve_gate(tid, gate["id"], "rejected")
        self.assertEqual(resolved["status"], "rejected")

        task = self._get_task(tid)
        self.assertEqual(task.status, "failed")


if __name__ == "__main__":
    unittest.main()
