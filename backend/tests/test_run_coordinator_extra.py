"""Tests for app/services/run_coordinator.py.

The service imports `engine` directly from app.db, so we patch it to use
an in-memory SQLite engine for test isolation.
"""

import unittest
import uuid
from unittest.mock import patch

from sqlmodel import Session, SQLModel, create_engine

import app.services.run_coordinator as rc
from app.models import AgentRun, RunCheckpoint


def _make_test_engine():
    engine = create_engine("sqlite://")
    SQLModel.metadata.drop_all(engine, checkfirst=True)
    SQLModel.metadata.create_all(engine)
    return engine


class RunCoordinatorTestCase(unittest.TestCase):
    def setUp(self):
        self.engine = _make_test_engine()
        self._patch = patch.object(rc, "engine", self.engine)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self.engine.dispose()

    # ------------------------------------------------------------------

    def test_start_run_creates_row(self):
        run = rc.start_run(owner_subject="alice@example.com", runtime_kind="claude_code")
        self.assertIsNotNone(run.id)
        self.assertEqual(run.owner_subject, "alice@example.com")
        self.assertEqual(run.status, "starting")
        # Verify it persisted
        fetched = rc.get_run(run.id, "alice@example.com")
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.id, run.id)

    def test_start_run_idempotent(self):
        key = str(uuid.uuid4())
        run1 = rc.start_run(owner_subject="alice@example.com", runtime_kind="claude_code", idempotency_key=key)
        run2 = rc.start_run(owner_subject="alice@example.com", runtime_kind="claude_code", idempotency_key=key)
        self.assertEqual(run1.id, run2.id)

    def test_checkpoint_appends_monotonic_seq(self):
        run = rc.start_run(owner_subject="alice@example.com", runtime_kind="claude_code")
        cp0 = rc.checkpoint(run.id, "turn", {"msg": "first"}, "alice@example.com")
        cp1 = rc.checkpoint(run.id, "turn", {"msg": "second"}, "alice@example.com")
        self.assertEqual(cp0.seq, 0)
        self.assertEqual(cp1.seq, 1)

    def test_resume_valid_token(self):
        run = rc.start_run(owner_subject="alice@example.com", runtime_kind="claude_code")
        rc.checkpoint(run.id, "turn", {"step": 1}, "alice@example.com")
        result = rc.resume(run.id, run.resume_token, "alice@example.com")
        self.assertIsNotNone(result)
        self.assertNotIn("error", result)
        self.assertIsNotNone(result["last_checkpoint"])
        self.assertEqual(result["last_checkpoint"].seq, 0)

    def test_resume_no_checkpoints(self):
        run = rc.start_run(owner_subject="alice@example.com", runtime_kind="claude_code")
        result = rc.resume(run.id, run.resume_token, "alice@example.com")
        self.assertIsNotNone(result)
        self.assertIsNone(result["last_checkpoint"])

    def test_resume_invalid_token(self):
        run = rc.start_run(owner_subject="alice@example.com", runtime_kind="claude_code")
        result = rc.resume(run.id, "wrong-token", "alice@example.com")
        self.assertIsNotNone(result)
        self.assertIn("error", result)
        self.assertEqual(result["error"], "invalid_resume_token")

    def test_transition_terminal_sets_ended_at(self):
        run = rc.start_run(owner_subject="alice@example.com", runtime_kind="claude_code")
        self.assertIsNone(run.ended_at)
        updated = rc.transition(run.id, "completed", "alice@example.com")
        self.assertEqual(updated.status, "completed")
        self.assertIsNotNone(updated.ended_at)

    def test_transition_non_terminal_no_ended_at(self):
        run = rc.start_run(owner_subject="alice@example.com", runtime_kind="claude_code")
        updated = rc.transition(run.id, "running", "alice@example.com")
        self.assertEqual(updated.status, "running")
        self.assertIsNone(updated.ended_at)

    def test_list_runs_scoped_by_owner(self):
        rc.start_run(owner_subject="alice@example.com", runtime_kind="claude_code")
        rc.start_run(owner_subject="alice@example.com", runtime_kind="claude_code")
        rc.start_run(owner_subject="bob@example.com", runtime_kind="claude_code")

        alice_runs = rc.list_runs("alice@example.com")
        bob_runs = rc.list_runs("bob@example.com")

        self.assertEqual(len(alice_runs), 2)
        self.assertEqual(len(bob_runs), 1)
        self.assertTrue(all(r.owner_subject == "alice@example.com" for r in alice_runs))

    def test_list_checkpoints_scoped_by_owner(self):
        run = rc.start_run(owner_subject="alice@example.com", runtime_kind="claude_code")
        rc.checkpoint(run.id, "turn", {"x": 1}, "alice@example.com")
        rc.checkpoint(run.id, "turn", {"x": 2}, "alice@example.com")

        # alice can see them
        cps = rc.list_checkpoints(run.id, "alice@example.com")
        self.assertEqual(len(cps), 2)

        # bob cannot
        cps_bob = rc.list_checkpoints(run.id, "bob@example.com")
        self.assertEqual(len(cps_bob), 0)

    def test_transition_unknown_run_raises(self):
        with self.assertRaises(ValueError):
            rc.transition("nonexistent-id", "completed", "alice@example.com")


if __name__ == "__main__":
    unittest.main()
