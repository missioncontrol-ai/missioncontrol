"""Tests for concurrent claim safety, lease_id validation, and version_counter.

Uses the same in-memory SQLite + patch pattern as test_work.py.
For the concurrency test we use ThreadPoolExecutor with direct function calls
(no HTTP server needed — the function is called directly and each thread gets
its own session via the patched get_session).
"""

import json
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import app.routers.work as work
from app.models import Kluster, MeshTask

SUBJECT = "agent@example.com"


def _req(subject: str = SUBJECT):
    return SimpleNamespace(
        state=SimpleNamespace(principal={"subject": subject, "email": subject})
    )


class ClaimRaceTestCase(unittest.TestCase):
    def setUp(self):
        # StaticPool keeps a single shared connection — required so all threads
        # see the same in-memory SQLite database.
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.drop_all(self.engine, checkfirst=True)
        SQLModel.metadata.create_all(self.engine)
        self._patches = [
            patch.object(work, "get_session", self._session_scope()),
            patch.object(work, "actor_subject_from_request", lambda _req: SUBJECT),
            patch.object(work, "engine", self.engine),
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
            s.add(Kluster(
                id=kid,
                mission_id=mission_id,
                name="test kluster",
                owners="agent@example.com",
            ))
            s.commit()
        return kid

    def _make_task(self, kluster_id: str, *, status: str = "ready") -> str:
        tid = str(uuid.uuid4())
        with Session(self.engine) as s:
            s.add(MeshTask(
                id=tid,
                kluster_id=kluster_id,
                mission_id="mission-1",
                title="task",
                description="",
                input_json="{}",
                claim_policy="first_claim",
                depends_on=json.dumps([]),
                produces="{}",
                consumes="{}",
                required_capabilities="[]",
                status=status,
                priority=0,
                created_by_subject=SUBJECT,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            ))
            s.commit()
        return tid

    def _get_task(self, task_id: str) -> MeshTask:
        with Session(self.engine) as s:
            return s.get(MeshTask, task_id)

    # ------------------------------------------------------------------

    def test_only_one_claim_wins(self):
        """Fire 20 concurrent claims; exactly one should succeed."""
        kid = self._make_kluster()
        tid = self._make_task(kid)

        successes = []
        failures = []

        def attempt_claim():
            try:
                result = work.claim_task(tid, _req())
                return ("ok", result)
            except HTTPException as e:
                return ("err", e.status_code)

        with ThreadPoolExecutor(max_workers=20) as executor:
            futures = [executor.submit(attempt_claim) for _ in range(20)]
            for f in as_completed(futures):
                kind, val = f.result()
                if kind == "ok":
                    successes.append(val)
                else:
                    failures.append(val)

        self.assertEqual(len(successes), 1, f"Expected 1 success, got {len(successes)}")
        self.assertEqual(len(failures), 19)
        for code in failures:
            self.assertIn(code, (409, 423), f"Unexpected status code {code}")

    def test_claim_lease_id_returned(self):
        """Single successful claim returns a non-null claim_lease_id."""
        kid = self._make_kluster()
        tid = self._make_task(kid)
        result = work.claim_task(tid, _req())
        self.assertIn("claim_lease_id", result)
        self.assertIsNotNone(result["claim_lease_id"])
        self.assertIsInstance(result["claim_lease_id"], str)
        self.assertTrue(len(result["claim_lease_id"]) > 0)

    def test_heartbeat_wrong_lease_id_rejected(self):
        """Heartbeat with a wrong lease_id must be rejected with 409."""
        kid = self._make_kluster()
        tid = self._make_task(kid)
        work.claim_task(tid, _req())

        wrong_lease = str(uuid.uuid4())
        with self.assertRaises(HTTPException) as ctx:
            work.heartbeat_task(tid, _req(), body=work.HeartbeatBody(claim_lease_id=wrong_lease))
        self.assertEqual(ctx.exception.status_code, 409)

    def test_heartbeat_no_lease_id_allowed(self):
        """Heartbeat with no claim_lease_id in body is backward-compat: should return 200."""
        kid = self._make_kluster()
        tid = self._make_task(kid)
        work.claim_task(tid, _req())

        # No claim_lease_id → should succeed
        result = work.heartbeat_task(tid, _req(), body=work.HeartbeatBody())
        self.assertIn("lease_expires_at", result)

    def test_complete_wrong_lease_rejected(self):
        """Completing a task with a wrong lease_id must be rejected with 409."""
        kid = self._make_kluster()
        tid = self._make_task(kid, status="running")
        # Manually assign a known lease so we can pass a wrong one
        known_lease = str(uuid.uuid4())
        with Session(self.engine) as s:
            t = s.get(MeshTask, tid)
            t.claim_lease_id = known_lease
            s.commit()

        wrong_lease = str(uuid.uuid4())
        with self.assertRaises(HTTPException) as ctx:
            work.complete_task(tid, body=work.CompleteBody(claim_lease_id=wrong_lease))
        self.assertEqual(ctx.exception.status_code, 409)

    def test_version_counter_increments(self):
        """After a successful claim, version_counter should be > 0."""
        kid = self._make_kluster()
        tid = self._make_task(kid)
        work.claim_task(tid, _req())
        task = self._get_task(tid)
        self.assertGreater(task.version_counter, 0)


if __name__ == "__main__":
    unittest.main()
