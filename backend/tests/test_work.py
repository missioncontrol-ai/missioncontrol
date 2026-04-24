"""Tests for the mc-mesh work model endpoints (app/routers/work.py).

Patterns follow the existing test suite:
  - unittest.TestCase
  - In-memory SQLite via create_engine("sqlite://")
  - patch get_session and actor_subject_from_request on the work module
"""

import json
import unittest
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from sqlmodel import Session, SQLModel, create_engine

import app.routers.work as work
from app.models import (
    Kluster,
    MeshAgent,
    MeshMessage,
    MeshProgressEvent,
    MeshTask,
)

SUBJECT = "agent@example.com"


def _req(subject: str = SUBJECT):
    return SimpleNamespace(
        state=SimpleNamespace(principal={"subject": subject, "email": subject})
    )


class WorkTestCase(unittest.TestCase):
    """Base class: spins up an in-memory SQLite DB and patches work module."""

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

    # ------------------------------------------------------------------
    # Fixtures
    # ------------------------------------------------------------------

    def _make_kluster(self, mission_id: str = "mission-1") -> str:
        kid = str(uuid.uuid4())
        with Session(self.engine) as s:
            s.add(
                Kluster(
                    id=kid,
                    mission_id=mission_id,
                    name="test kluster",
                    owners="agent@example.com",
                )
            )
            s.commit()
        return kid

    def _make_task(
        self,
        kluster_id: str,
        *,
        title: str = "task",
        status: str = "ready",
        claim_policy: str = "first_claim",
        depends_on: list | None = None,
        mission_id: str = "mission-1",
    ) -> str:
        tid = str(uuid.uuid4())
        with Session(self.engine) as s:
            s.add(
                MeshTask(
                    id=tid,
                    kluster_id=kluster_id,
                    mission_id=mission_id,
                    title=title,
                    description="",
                    input_json="{}",
                    claim_policy=claim_policy,
                    depends_on=json.dumps(depends_on or []),
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

    def _make_agent(self, mission_id: str = "mission-1") -> str:
        aid = str(uuid.uuid4())
        with Session(self.engine) as s:
            s.add(
                MeshAgent(
                    id=aid,
                    mission_id=mission_id,
                    runtime_kind="claude_code",
                    runtime_version="1.0",
                    capabilities="[]",
                    labels="{}",
                    status="offline",
                    enrolled_by_subject=SUBJECT,
                    enrolled_at=datetime.utcnow(),
                )
            )
            s.commit()
        return aid

    def _get_task(self, task_id: str) -> MeshTask:
        with Session(self.engine) as s:
            return s.get(MeshTask, task_id)

    def _get_agent(self, agent_id: str) -> MeshAgent:
        with Session(self.engine) as s:
            return s.get(MeshAgent, agent_id)


# ======================================================================
# Task creation
# ======================================================================


class TestTaskCreate(WorkTestCase):
    def test_creates_ready_task_with_no_deps(self):
        kid = self._make_kluster()
        result = work.create_task(
            kid,
            work.MeshTaskCreate(title="hello", description="world"),
            _req(),
        )
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["title"], "hello")
        self.assertEqual(result["kluster_id"], kid)

    def test_creates_blocked_task_with_deps(self):
        kid = self._make_kluster()
        dep_id = self._make_task(kid, title="dep")
        result = work.create_task(
            kid,
            work.MeshTaskCreate(title="child", depends_on=[dep_id]),
            _req(),
        )
        self.assertEqual(result["status"], "blocked")
        self.assertIn(dep_id, result["depends_on"])

    def test_rejects_unknown_dep_id(self):
        from fastapi import HTTPException
        kid = self._make_kluster()
        with self.assertRaises(HTTPException) as ctx:
            work.create_task(
                kid,
                work.MeshTaskCreate(title="bad", depends_on=["nonexistent-id"]),
                _req(),
            )
        self.assertEqual(ctx.exception.status_code, 400)

    def test_rejects_cycle(self):
        from fastapi import HTTPException
        kid = self._make_kluster()
        # A depends on B, B depends on A — cycle
        a_id = self._make_task(kid, title="A")
        b_id = self._make_task(kid, title="B", depends_on=[a_id])
        # Now try to make A depend on B (completing the cycle)
        with Session(self.engine) as s:
            a = s.get(MeshTask, a_id)
            a.depends_on = json.dumps([b_id])
            s.commit()
        with self.assertRaises(HTTPException) as ctx:
            work.create_task(
                kid,
                work.MeshTaskCreate(title="cycle", depends_on=[b_id]),
                _req(),
            )
        # Either a 400 (cycle detected for new task) is fine; main thing is it rejects
        self.assertIn(ctx.exception.status_code, (400,))

    def test_rejects_invalid_claim_policy(self):
        from fastapi import HTTPException
        kid = self._make_kluster()
        with self.assertRaises(HTTPException) as ctx:
            work.create_task(
                kid,
                work.MeshTaskCreate(title="bad", claim_policy="invalid"),
                _req(),
            )
        self.assertEqual(ctx.exception.status_code, 400)

    def test_task_inherits_mission_id_from_kluster(self):
        kid = self._make_kluster(mission_id="my-mission")
        result = work.create_task(
            kid, work.MeshTaskCreate(title="t"), _req()
        )
        self.assertEqual(result["mission_id"], "my-mission")


# ======================================================================
# Task lifecycle
# ======================================================================


class TestTaskLifecycle(WorkTestCase):
    def test_claim_transitions_to_claimed(self):
        kid = self._make_kluster()
        tid = self._make_task(kid)
        result = work.claim_task(tid, _req())
        self.assertEqual(result["task_id"], tid)
        self.assertIn("lease_expires_at", result)
        t = self._get_task(tid)
        self.assertEqual(t.status, "claimed")

    def test_claim_exclusive_second_claim_fails(self):
        from fastapi import HTTPException
        kid = self._make_kluster()
        tid = self._make_task(kid)
        work.claim_task(tid, _req())
        with self.assertRaises(HTTPException) as ctx:
            work.claim_task(tid, _req())
        # 423 = task not available (already claimed); 409 = concurrent CAS race
        self.assertIn(ctx.exception.status_code, (409, 423))

    def test_broadcast_claim_allows_multiple(self):
        kid = self._make_kluster()
        tid = self._make_task(kid, claim_policy="broadcast")
        work.claim_task(tid, _req())
        # Second claim on same broadcast task must succeed
        result2 = work.claim_task(tid, _req())
        self.assertEqual(result2["task_id"], tid)
        t = self._get_task(tid)
        self.assertEqual(t.status, "running")

    def test_first_progress_event_transitions_to_running(self):
        kid = self._make_kluster()
        tid = self._make_task(kid, status="claimed")
        work.append_progress(
            tid,
            work.MeshProgressEventCreate(event_type="info", summary="hi"),
            _req(),
        )
        t = self._get_task(tid)
        self.assertEqual(t.status, "running")

    def test_heartbeat_renews_lease(self):
        kid = self._make_kluster()
        tid = self._make_task(kid, status="claimed")
        # set an existing lease
        with Session(self.engine) as s:
            t = s.get(MeshTask, tid)
            t.lease_expires_at = datetime.utcnow() + timedelta(seconds=10)
            s.commit()
        before = self._get_task(tid).lease_expires_at
        work.heartbeat_task(tid, _req())
        after = self._get_task(tid).lease_expires_at
        self.assertGreater(after, before)

    def test_complete_transitions_to_finished(self):
        kid = self._make_kluster()
        tid = self._make_task(kid, status="running")
        result = work.complete_task(tid)
        self.assertEqual(result["status"], "finished")
        self.assertIsNone(result["lease_expires_at"])

    def test_fail_transitions_to_failed(self):
        kid = self._make_kluster()
        tid = self._make_task(kid, status="running")
        result = work.fail_task(tid)
        self.assertEqual(result["status"], "failed")

    def test_cancel_transitions_to_cancelled(self):
        kid = self._make_kluster()
        tid = self._make_task(kid)
        result = work.cancel_task(tid, _req())
        self.assertEqual(result["status"], "cancelled")

    def test_cancel_already_finished_raises(self):
        from fastapi import HTTPException
        kid = self._make_kluster()
        tid = self._make_task(kid, status="finished")
        with self.assertRaises(HTTPException) as ctx:
            work.cancel_task(tid, _req())
        self.assertEqual(ctx.exception.status_code, 409)

    def test_retry_failed_task(self):
        kid = self._make_kluster()
        tid = self._make_task(kid, status="failed")
        result = work.retry_task(tid, _req())
        self.assertEqual(result["status"], "ready")

    def test_retry_non_failed_raises(self):
        from fastapi import HTTPException
        kid = self._make_kluster()
        tid = self._make_task(kid, status="running")
        with self.assertRaises(HTTPException):
            work.retry_task(tid, _req())


# ======================================================================
# DAG unblocking
# ======================================================================


class TestDagUnblocking(WorkTestCase):
    def test_completing_dep_unblocks_child(self):
        kid = self._make_kluster()
        dep_id = self._make_task(kid, title="A", status="running")
        child_id = self._make_task(kid, title="B", status="pending", depends_on=[dep_id])

        work.complete_task(dep_id)

        child = self._get_task(child_id)
        self.assertEqual(child.status, "ready")

    def test_partial_deps_stay_pending(self):
        kid = self._make_kluster()
        dep_a = self._make_task(kid, title="A", status="running")
        dep_b = self._make_task(kid, title="B", status="ready")
        child = self._make_task(
            kid, title="C", status="pending", depends_on=[dep_a, dep_b]
        )

        # Only A finishes — B is still pending
        work.complete_task(dep_a)

        c = self._get_task(child)
        self.assertEqual(c.status, "pending")

    def test_all_deps_done_unblocks_child(self):
        kid = self._make_kluster()
        dep_a = self._make_task(kid, title="A", status="running")
        dep_b = self._make_task(kid, title="B", status="running")
        child = self._make_task(
            kid, title="C", status="pending", depends_on=[dep_a, dep_b]
        )

        work.complete_task(dep_a)
        c = self._get_task(child)
        self.assertEqual(c.status, "pending")  # B still running

        work.complete_task(dep_b)
        c = self._get_task(child)
        self.assertEqual(c.status, "ready")

    def test_chain_a_b_c_unblocks_in_sequence(self):
        kid = self._make_kluster()
        a = self._make_task(kid, title="A", status="running")
        b = self._make_task(kid, title="B", status="pending", depends_on=[a])
        c = self._make_task(kid, title="C", status="pending", depends_on=[b])

        work.complete_task(a)
        self.assertEqual(self._get_task(b).status, "ready")
        self.assertEqual(self._get_task(c).status, "pending")  # B not done yet


# ======================================================================
# Lease expiry
# ======================================================================


class TestLeaseExpiry(WorkTestCase):
    def _expire_task(self, task_id: str):
        """Force a task's lease to be in the past."""
        with Session(self.engine) as s:
            t = s.get(MeshTask, task_id)
            t.status = "claimed"
            t.lease_expires_at = datetime.utcnow() - timedelta(seconds=1)
            s.commit()

    def test_expired_claimed_task_returns_to_ready_on_list(self):
        kid = self._make_kluster()
        tid = self._make_task(kid)
        self._expire_task(tid)

        result = work.list_tasks(kid)
        statuses = {t["id"]: t["status"] for t in result}
        self.assertEqual(statuses[tid], "ready")

    def test_expired_lease_not_auto_reaped_on_claim(self):
        """Expired leases are no longer reaped inline during claim (watchdog handles that).

        A task with an expired lease that hasn't been reaped yet will return 423,
        not silently succeed. The work_watchdog service (Task 3) reaps leases.
        """
        from fastapi import HTTPException
        kid = self._make_kluster()
        tid = self._make_task(kid)
        self._expire_task(tid)

        # claim_task returns 423 — task still shows as "claimed" until watchdog reaps it
        with self.assertRaises(HTTPException) as ctx:
            work.claim_task(tid, _req())
        self.assertEqual(ctx.exception.status_code, 423)

    def test_broadcast_task_not_expired(self):
        """Broadcast tasks should never be expired by _expire_stale_leases."""
        kid = self._make_kluster()
        tid = self._make_task(kid, claim_policy="broadcast")
        with Session(self.engine) as s:
            t = s.get(MeshTask, tid)
            t.status = "running"
            t.lease_expires_at = datetime.utcnow() - timedelta(seconds=1)
            s.commit()

        work.list_tasks(kid)  # triggers _expire_stale_leases
        t = self._get_task(tid)
        self.assertEqual(t.status, "running")  # unchanged


# ======================================================================
# Agent pool
# ======================================================================


class TestAgentPool(WorkTestCase):
    def test_enroll_creates_offline_agent(self):
        result = work.enroll_agent(
            "mission-1",
            work.MeshAgentEnroll(runtime_kind="claude_code", capabilities=["code.edit"]),
            _req(),
        )
        self.assertEqual(result["status"], "offline")
        self.assertEqual(result["runtime_kind"], "claude_code")
        self.assertEqual(result["mission_id"], "mission-1")

    def test_heartbeat_transitions_offline_to_idle(self):
        aid = self._make_agent()
        result = work.agent_heartbeat(aid)
        self.assertEqual(result["status"], "idle")
        agent = self._get_agent(aid)
        self.assertIsNotNone(agent.last_heartbeat_at)

    def test_heartbeat_leaves_busy_agent_busy(self):
        aid = self._make_agent()
        with Session(self.engine) as s:
            a = s.get(MeshAgent, aid)
            a.status = "busy"
            s.commit()
        result = work.agent_heartbeat(aid)
        self.assertEqual(result["status"], "busy")

    def test_set_status_updates_agent(self):
        aid = self._make_agent()
        work.set_agent_status(aid, status="busy")
        self.assertEqual(self._get_agent(aid).status, "busy")

    def test_set_status_rejects_invalid(self):
        from fastapi import HTTPException
        aid = self._make_agent()
        with self.assertRaises(HTTPException):
            work.set_agent_status(aid, status="flying")

    def test_list_agents_filters_by_mission(self):
        self._make_agent(mission_id="m1")
        self._make_agent(mission_id="m1")
        self._make_agent(mission_id="m2")
        result = work.list_agents("m1")
        self.assertEqual(len(result), 2)
        self.assertTrue(all(a["mission_id"] == "m1" for a in result))


# ======================================================================
# Progress events
# ======================================================================


class TestProgressEvents(WorkTestCase):
    def test_append_and_retrieve_progress(self):
        kid = self._make_kluster()
        tid = self._make_task(kid, status="claimed")

        work.append_progress(
            tid,
            work.MeshProgressEventCreate(event_type="phase_started", phase="running", summary="go"),
            _req(),
        )
        work.append_progress(
            tid,
            work.MeshProgressEventCreate(event_type="info", summary="step 1"),
            _req(),
        )

        events = work.get_task_progress(tid)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["event_type"], "phase_started")
        self.assertEqual(events[1]["event_type"], "info")

    def test_seq_is_monotonic(self):
        kid = self._make_kluster()
        tid = self._make_task(kid, status="claimed")
        for i in range(5):
            work.append_progress(
                tid,
                work.MeshProgressEventCreate(event_type="info", summary=f"step {i}"),
                _req(),
            )
        events = work.get_task_progress(tid)
        seqs = [e["seq"] for e in events]
        self.assertEqual(seqs, sorted(seqs))
        self.assertEqual(seqs, list(range(5)))

    def test_since_seq_filters_events(self):
        kid = self._make_kluster()
        tid = self._make_task(kid, status="claimed")
        for i in range(4):
            work.append_progress(
                tid,
                work.MeshProgressEventCreate(event_type="info", summary=f"s{i}"),
                _req(),
            )
        events = work.get_task_progress(tid, since_seq=1)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["seq"], 2)


# ======================================================================
# Kluster graph
# ======================================================================


class TestKlusterGraph(WorkTestCase):
    def test_graph_returns_nodes_and_edges(self):
        kid = self._make_kluster()
        a = self._make_task(kid, title="A")
        b = self._make_task(kid, title="B", depends_on=[a])
        c = self._make_task(kid, title="C", depends_on=[a])

        graph = work.task_graph(kid)
        node_ids = {n["id"] for n in graph["nodes"]}
        self.assertIn(a, node_ids)
        self.assertIn(b, node_ids)
        self.assertIn(c, node_ids)

        edges = {(e["from"], e["to"]) for e in graph["edges"]}
        self.assertIn((a, b), edges)
        self.assertIn((a, c), edges)

    def test_graph_no_edges_when_no_deps(self):
        kid = self._make_kluster()
        self._make_task(kid, title="standalone")
        graph = work.task_graph(kid)
        self.assertEqual(graph["edges"], [])


# ======================================================================
# Messaging
# ======================================================================


class TestMessaging(WorkTestCase):
    def test_send_and_list_mission_messages(self):
        work.send_mission_message(
            "mission-1",
            work.MeshMessageCreate(channel="coordination", body_json='{"text":"hi"}'),
            _req(),
        )
        msgs = work.list_mission_messages("mission-1")
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["channel"], "coordination")

    def test_send_and_list_kluster_messages(self):
        kid = self._make_kluster(mission_id="m1")
        work.send_kluster_message(
            kid,
            work.MeshMessageCreate(channel="handoff", body_json='{"text":"done"}'),
            _req(),
        )
        msgs = work.list_kluster_messages(kid)
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["channel"], "handoff")

    def test_agent_inbox_filters_by_to_agent(self):
        # Two messages: one to agent-A, one broadcast (no to_agent_id)
        with Session(self.engine) as s:
            s.add(MeshMessage(
                mission_id="m1", from_agent_id="agent-B", to_agent_id="agent-A",
                channel="coordination", body_json='{}', created_at=datetime.utcnow(),
            ))
            s.add(MeshMessage(
                mission_id="m1", from_agent_id="agent-B", to_agent_id=None,
                channel="coordination", body_json='{}', created_at=datetime.utcnow(),
            ))
            s.add(MeshMessage(
                mission_id="m1", from_agent_id="agent-B", to_agent_id="agent-C",
                channel="coordination", body_json='{}', created_at=datetime.utcnow(),
            ))
            s.commit()

        msgs = work.get_agent_messages("agent-A")
        self.assertEqual(len(msgs), 2)  # direct + broadcast, not agent-C's message

    def test_since_id_pagination(self):
        kid = self._make_kluster()
        for i in range(5):
            work.send_kluster_message(
                kid,
                work.MeshMessageCreate(body_json=f'{{"n":{i}}}'),
                _req(),
            )
        all_msgs = work.list_kluster_messages(kid)
        pivot_id = all_msgs[2]["id"]
        newer = work.list_kluster_messages(kid, since_id=pivot_id)
        self.assertEqual(len(newer), 2)


# ======================================================================
# _detect_cycle (unit tests of the helper directly)
# ======================================================================


class TestDetectCycle(WorkTestCase):
    def test_no_cycle_linear_chain(self):
        kid = self._make_kluster()
        a = self._make_task(kid, title="A")
        b = self._make_task(kid, title="B", depends_on=[a])
        new_id = str(uuid.uuid4())
        with Session(self.engine) as s:
            self.assertFalse(work._detect_cycle(kid, new_id, [b], s))

    def test_detects_direct_cycle(self):
        kid = self._make_kluster()
        a = self._make_task(kid, title="A")
        b_id = str(uuid.uuid4())
        # Inject B with depends_on=[a] manually before checking if A can depend on B
        with Session(self.engine) as s:
            s.add(MeshTask(
                id=b_id, kluster_id=kid, mission_id="m", title="B",
                description="", input_json="{}", claim_policy="first_claim",
                depends_on=json.dumps([a]), produces="{}", consumes="{}",
                required_capabilities="[]", status="ready", priority=0,
                created_by_subject=SUBJECT, created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            ))
            s.commit()
        with Session(self.engine) as s:
            # A trying to depend on B would create A→B→A
            self.assertTrue(work._detect_cycle(kid, a, [b_id], s))

    def test_no_cycle_parallel_tasks(self):
        kid = self._make_kluster()
        a = self._make_task(kid, title="A")
        b = self._make_task(kid, title="B")
        c_id = str(uuid.uuid4())
        with Session(self.engine) as s:
            # C depending on both A and B is fine (fork-join, not a cycle)
            self.assertFalse(work._detect_cycle(kid, c_id, [a, b], s))
