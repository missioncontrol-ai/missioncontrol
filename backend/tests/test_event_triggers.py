"""Tests for EventTrigger and scheduler mesh_task target."""
import asyncio
import json
import unittest
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from unittest.mock import patch

from sqlmodel import Session, SQLModel, select, create_engine

import app.services.agent_scheduler as sched_module
from app.models import (
    EventTrigger,
    Kluster,
    MeshTask,
    ScheduledAgentJob,
)
from app.services.agent_scheduler import (
    _create_mesh_task_from_spec,
    _evaluate_triggers,
    _predicate_matches,
    _run_job,
)

SUBJECT = "test@example.com"


class EventTriggerTestCase(unittest.TestCase):
    """Base: in-memory SQLite + patches for agent_scheduler and mesh_events."""

    def setUp(self):
        self.engine = create_engine("sqlite://")
        SQLModel.metadata.drop_all(self.engine, checkfirst=True)
        SQLModel.metadata.create_all(self.engine)

        # Patch get_session in agent_scheduler to use our in-memory engine
        engine = self.engine

        @contextmanager
        def _session_ctx():
            with Session(engine) as s:
                yield s

        self._patches = [
            patch.object(sched_module, "get_session" if hasattr(sched_module, "get_session") else "_noop", _session_ctx, create=True),
        ]
        # patch get_session in agent_scheduler module
        import app.db as db_module
        self._orig_get_session = db_module.get_session

        @contextmanager
        def fake_get_session():
            with Session(engine) as s:
                yield s

        db_module.get_session = fake_get_session
        self._db_module = db_module

        # Suppress mesh event publish (no running event loop needed)
        from app.services import mesh_events
        self._orig_publish = mesh_events.publish_task_event
        self._published_events = []

        def fake_publish(event_type, task_id, kluster_id, mission_id, **kw):
            self._published_events.append({
                "event": event_type,
                "task_id": task_id,
                "kluster_id": kluster_id,
                "mission_id": mission_id,
                **kw,
            })
            # Also push to in-process subscribers
            from app.services.mesh_events import _publish_local
            event = {
                "type": "task_event",
                "event": event_type,
                "task_id": task_id,
                "kluster_id": kluster_id,
                "mission_id": mission_id,
                **kw,
            }
            _publish_local(f"kluster:{kluster_id}", event)
            _publish_local(f"mission:{mission_id}", event)
            _publish_local("__all_events__", event)

        mesh_events.publish_task_event = fake_publish
        self._mesh_events = mesh_events

    def tearDown(self):
        self._db_module.get_session = self._orig_get_session
        self._mesh_events.publish_task_event = self._orig_publish

    def _make_kluster(self, mission_id: str = "mission-1") -> str:
        kid = str(uuid.uuid4())
        with Session(self.engine) as s:
            s.add(Kluster(
                id=kid,
                mission_id=mission_id,
                name="test-kluster",
                owners=SUBJECT,
            ))
            s.commit()
        return kid

    def _make_trigger(self, event_type: str, predicate: dict | None = None,
                      target_spec: dict | None = None, cooldown: int = 0) -> EventTrigger:
        kluster_id = self._make_kluster()
        spec = target_spec or {"kluster_id": kluster_id, "mission_id": "mission-1",
                               "title": "Triggered task", "description": ""}
        now = datetime.utcnow()
        trigger = EventTrigger(
            id=str(uuid.uuid4()),
            owner_subject=SUBJECT,
            event_type=event_type,
            predicate_json=json.dumps(predicate) if predicate else None,
            target_type="mesh_task",
            target_spec_json=json.dumps(spec),
            cooldown_seconds=cooldown,
            active=True,
            created_at=now,
            updated_at=now,
        )
        with Session(self.engine) as s:
            s.add(trigger)
            s.commit()
            s.refresh(trigger)
        return trigger

    # ------------------------------------------------------------------
    # predicate helper
    # ------------------------------------------------------------------

    def test_predicate_matches_all_keys(self):
        self.assertTrue(_predicate_matches({"kluster_id": "k1"}, {"kluster_id": "k1", "event": "task_completed"}))

    def test_predicate_rejects_mismatch(self):
        self.assertFalse(_predicate_matches({"kluster_id": "k1"}, {"kluster_id": "k2"}))

    def test_predicate_empty_matches_everything(self):
        self.assertTrue(_predicate_matches({}, {"kluster_id": "k1"}))

    # ------------------------------------------------------------------
    # _evaluate_triggers
    # ------------------------------------------------------------------

    def test_event_trigger_fires_on_matching_event(self):
        kluster_id = self._make_kluster()
        self._make_trigger(
            event_type="task_completed",
            predicate={"kluster_id": kluster_id},
            target_spec={"kluster_id": kluster_id, "mission_id": "m1", "title": "T"},
        )

        event = {"event": "task_completed", "kluster_id": kluster_id, "task_id": "t1", "mission_id": "m1"}
        asyncio.run(_evaluate_triggers(event))

        with Session(self.engine) as s:
            tasks = s.exec(select(MeshTask).where(MeshTask.kluster_id == kluster_id)).all()
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].status, "ready")

    def test_predicate_filter_rejects_non_match(self):
        kluster_id = self._make_kluster()
        self._make_trigger(
            event_type="task_completed",
            predicate={"kluster_id": "other-kluster"},
            target_spec={"kluster_id": kluster_id, "mission_id": "m1", "title": "T"},
        )

        event = {"event": "task_completed", "kluster_id": kluster_id, "task_id": "t1", "mission_id": "m1"}
        asyncio.run(_evaluate_triggers(event))

        with Session(self.engine) as s:
            tasks = s.exec(select(MeshTask).where(MeshTask.kluster_id == kluster_id)).all()
        self.assertEqual(len(tasks), 0)

    def test_cooldown_prevents_double_fire(self):
        kluster_id = self._make_kluster()
        trigger = self._make_trigger(
            event_type="task_completed",
            target_spec={"kluster_id": kluster_id, "mission_id": "m1", "title": "T"},
            cooldown=3600,
        )

        event = {"event": "task_completed", "kluster_id": kluster_id, "task_id": "t1", "mission_id": "m1"}
        asyncio.run(_evaluate_triggers(event))
        asyncio.run(_evaluate_triggers(event))  # second call — should be blocked by cooldown

        with Session(self.engine) as s:
            tasks = s.exec(select(MeshTask).where(MeshTask.kluster_id == kluster_id)).all()
        self.assertEqual(len(tasks), 1, "Cooldown should prevent second fire")

    def test_inactive_trigger_not_fired(self):
        kluster_id = self._make_kluster()
        trigger = self._make_trigger(
            event_type="task_completed",
            target_spec={"kluster_id": kluster_id, "mission_id": "m1", "title": "T"},
        )
        with Session(self.engine) as s:
            t = s.get(EventTrigger, trigger.id)
            t.active = False
            s.add(t)
            s.commit()

        event = {"event": "task_completed", "kluster_id": kluster_id, "task_id": "t1", "mission_id": "m1"}
        asyncio.run(_evaluate_triggers(event))

        with Session(self.engine) as s:
            tasks = s.exec(select(MeshTask).where(MeshTask.kluster_id == kluster_id)).all()
        self.assertEqual(len(tasks), 0)

    # ------------------------------------------------------------------
    # ScheduledAgentJob mesh_task target
    # ------------------------------------------------------------------

    def test_scheduled_job_with_mesh_task_target(self):
        kluster_id = self._make_kluster()
        spec = {"kluster_id": kluster_id, "mission_id": "m1", "title": "Cron task"}
        now = datetime.utcnow()
        job = ScheduledAgentJob(
            owner_subject=SUBJECT,
            name="mesh-cron",
            cron_expr="0 * * * *",
            initial_prompt="",
            target_type="mesh_task",
            target_spec_json=json.dumps(spec),
            enabled=True,
            created_at=now,
            updated_at=now,
        )
        with Session(self.engine) as s:
            s.add(job)
            s.commit()
            s.refresh(job)
            job_id = job.id

        asyncio.run(_run_job(job_id))

        with Session(self.engine) as s:
            tasks = s.exec(select(MeshTask).where(MeshTask.kluster_id == kluster_id)).all()
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].title, "Cron task")
        self.assertEqual(tasks[0].status, "ready")

    def test_scheduled_job_ai_session_path_skipped_without_gateway(self):
        """ai_session jobs without mesh_task target should attempt gateway (which may fail) but not create MeshTask."""
        kluster_id = self._make_kluster()
        now = datetime.utcnow()
        job = ScheduledAgentJob(
            owner_subject=SUBJECT,
            name="ai-cron",
            cron_expr="0 * * * *",
            initial_prompt="hello",
            target_type="ai_session",
            enabled=True,
            created_at=now,
            updated_at=now,
        )
        with Session(self.engine) as s:
            s.add(job)
            s.commit()
            s.refresh(job)
            job_id = job.id

        # Gateway will fail in test env — that's expected; no MeshTask should be created
        asyncio.run(_run_job(job_id))

        with Session(self.engine) as s:
            tasks = s.exec(select(MeshTask)).all()
        self.assertEqual(len(tasks), 0)

    # ------------------------------------------------------------------
    # _create_mesh_task_from_spec
    # ------------------------------------------------------------------

    def test_create_mesh_task_from_spec_defaults(self):
        kluster_id = self._make_kluster()
        spec = {"kluster_id": kluster_id, "mission_id": "m1", "title": "T"}
        with Session(self.engine) as s:
            _create_mesh_task_from_spec(s, spec, SUBJECT)

        with Session(self.engine) as s:
            tasks = s.exec(select(MeshTask).where(MeshTask.kluster_id == kluster_id)).all()
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].claim_policy, "first_claim")
        self.assertEqual(tasks[0].priority, 0)
        self.assertEqual(tasks[0].created_by_subject, SUBJECT)
