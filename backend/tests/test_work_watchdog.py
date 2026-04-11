"""Tests for the centralized work watchdog (app/services/work_watchdog)."""
import unittest
import uuid
from datetime import datetime, timedelta

from sqlmodel import Session, SQLModel, create_engine, select

from app.models import MeshTask
from app.services.work_watchdog import _tick


def _make_engine():
    engine = create_engine("sqlite://")
    SQLModel.metadata.drop_all(engine, checkfirst=True)
    SQLModel.metadata.create_all(engine)
    return engine


def _add_task(session: Session, status: str, lease_expires_at=None, **kwargs) -> str:
    tid = str(uuid.uuid4())
    kwargs.setdefault("claim_policy", "single")
    task = MeshTask(
        id=tid,
        kluster_id="k1",
        mission_id="m1",
        title="test task",
        status=status,
        owner_subject="test@example.com",
        lease_expires_at=lease_expires_at,
        **kwargs,
    )
    session.add(task)
    session.commit()
    return tid


class TestWorkWatchdog(unittest.TestCase):
    def setUp(self):
        self.engine = _make_engine()

    def tearDown(self):
        self.engine.dispose()

    def test_expire_stale_claimed_task_returns_to_ready(self):
        with Session(self.engine) as session:
            tid = _add_task(
                session,
                status="claimed",
                claimed_by_agent_id="agent-1",
                claim_lease_id="lease-1",
                lease_expires_at=datetime.utcnow() - timedelta(minutes=5),
            )

        with Session(self.engine) as session:
            freed = _tick(session)

        self.assertEqual(freed, 1)

        with Session(self.engine) as session:
            task = session.exec(select(MeshTask).where(MeshTask.id == tid)).one()
            self.assertEqual(task.status, "ready")
            self.assertIsNone(task.claimed_by_agent_id)
            self.assertIsNone(task.claim_lease_id)
            self.assertIsNone(task.lease_expires_at)

    def test_expire_stale_running_task_returns_to_ready(self):
        with Session(self.engine) as session:
            tid = _add_task(
                session,
                status="running",
                claimed_by_agent_id="agent-2",
                claim_lease_id="lease-2",
                lease_expires_at=datetime.utcnow() - timedelta(seconds=1),
            )

        with Session(self.engine) as session:
            freed = _tick(session)

        self.assertEqual(freed, 1)

        with Session(self.engine) as session:
            task = session.exec(select(MeshTask).where(MeshTask.id == tid)).one()
            self.assertEqual(task.status, "ready")

    def test_active_lease_not_expired(self):
        with Session(self.engine) as session:
            _add_task(
                session,
                status="running",
                claimed_by_agent_id="agent-3",
                claim_lease_id="lease-3",
                lease_expires_at=datetime.utcnow() + timedelta(minutes=5),
            )

        with Session(self.engine) as session:
            freed = _tick(session)

        self.assertEqual(freed, 0)

    def test_ready_task_not_touched(self):
        with Session(self.engine) as session:
            _add_task(session, status="ready")

        with Session(self.engine) as session:
            freed = _tick(session)

        self.assertEqual(freed, 0)

    def test_broadcast_task_not_expired(self):
        """Broadcast tasks should never be expired by the watchdog."""
        with Session(self.engine) as session:
            _add_task(
                session,
                status="claimed",
                claim_policy="broadcast",
                claimed_by_agent_id="agent-4",
                lease_expires_at=datetime.utcnow() - timedelta(minutes=10),
            )

        with Session(self.engine) as session:
            freed = _tick(session)

        self.assertEqual(freed, 0)

    def test_multiple_stale_tasks_all_freed(self):
        with Session(self.engine) as session:
            for i in range(3):
                _add_task(
                    session,
                    status="claimed",
                    claimed_by_agent_id=f"agent-{i}",
                    claim_lease_id=f"lease-{i}",
                    lease_expires_at=datetime.utcnow() - timedelta(minutes=1),
                )

        with Session(self.engine) as session:
            freed = _tick(session)

        self.assertEqual(freed, 3)


if __name__ == "__main__":
    unittest.main()
