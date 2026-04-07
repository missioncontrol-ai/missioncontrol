"""Tests for the runtime fabric endpoints."""

import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

from app.routers.runtime import (
    JobCreate,
    LeaseComplete,
    LeaseCreate,
    LeaseStatus,
    NodeHeartbeat,
    NodeRegister,
    complete_lease,
    create_job,
    create_lease,
    heartbeat_node,
    list_jobs,
    list_nodes,
    register_node,
    update_lease_status,
)


SUBJECT = "test@example.com"


def _req(subject=SUBJECT):
    return SimpleNamespace(state=SimpleNamespace(principal={"subject": subject, "email": subject}))


class _Session:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.added = []

    def exec(self, *_args, **_kwargs):
        session = self

        class _Result:
            def first(self_inner):
                return session.rows[0] if session.rows else None

            def all(self_inner):
                return list(session.rows)

        return _Result()

    def get(self, _model, ident):
        for row in self.rows:
            if getattr(row, "id", None) == ident:
                return row
        return None

    def add(self, row):
        self.added.append(row)

    def commit(self):
        return None

    def refresh(self, _row):
        return None


def _session(rows=None):
    @contextmanager
    def _ctx():
        yield _Session(rows)

    return _ctx


def _node(node_id="node-1", owner=SUBJECT, node_name="node-a"):
    return SimpleNamespace(
        id=node_id,
        owner_subject=owner,
        node_name=node_name,
        hostname="host-a",
        status="online",
        trust_tier="untrusted",
        labels_json="{}",
        capacity_json="{}",
        capabilities_json="[]",
        runtime_version="",
        last_heartbeat_at=None,
        registered_at=None,
        updated_at=None,
    )


def _job(job_id="job-1", owner=SUBJECT):
    return SimpleNamespace(
        id=job_id,
        owner_subject=owner,
        mission_id="mission-1",
        task_id=None,
        runtime_session_id="",
        runtime_class="container",
        image="alpine:3",
        command="echo hi",
        args_json='["-lc","echo hi"]',
        env_json="{}",
        cwd="",
        mounts_json="[]",
        artifact_rules_json="{}",
        timeout_seconds=3600,
        restart_policy="never",
        required_capabilities_json="[]",
        preferred_labels_json="{}",
        status="queued",
        created_at=None,
        updated_at=None,
    )


def _lease(lease_id="lease-1", job_id="job-1", node_id="node-1"):
    return SimpleNamespace(
        id=lease_id,
        job_id=job_id,
        node_id=node_id,
        status="leased",
        claimed_at=None,
        heartbeat_at=None,
        started_at=None,
        finished_at=None,
        exit_code=None,
        error_message="",
        cleanup_status="pending",
        created_at=None,
        updated_at=None,
    )


class RuntimeFabricTests(unittest.TestCase):
    def test_register_list_and_heartbeat_node(self):
        with patch("app.routers.runtime.get_session", _session([])):
            result = register_node(
                body=NodeRegister(node_name="node-a", hostname="host-a", bootstrap_token="mcs_123"),
                request=_req(),
            )
        self.assertEqual(result["node_name"], "node-a")
        self.assertEqual(result["status"], "online")

        node = _node(node_id=result["id"], node_name="node-a")
        with patch("app.routers.runtime.get_session", _session([node])):
            listed = list_nodes(request=_req())
        self.assertEqual(len(listed["nodes"]), 1)

        with patch("app.routers.runtime.get_session", _session([node])):
            updated = heartbeat_node(
                node_id=node.id,
                body=NodeHeartbeat(status="degraded", runtime_version="mc-1"),
                request=_req(),
            )
        self.assertEqual(updated["status"], "degraded")
        self.assertEqual(updated["runtime_version"], "mc-1")

    def test_create_job_lease_and_complete(self):
        with patch("app.routers.runtime.get_session", _session([])):
            job = create_job(
                body=JobCreate(
                    mission_id="mission-1",
                    runtime_class="container",
                    image="alpine:3",
                    command="echo hi",
                    args=["-lc", "echo hi"],
                ),
                request=_req(),
            )
        self.assertEqual(job["status"], "queued")

        node = _node()
        job_row = _job(job_id=job["id"])
        lease_row = _lease(job_id=job["id"], node_id=node.id)
        with patch("app.routers.runtime.get_session", _session([job_row, node, lease_row])):
            created_lease = create_lease(
                job_id=job["id"],
                body=LeaseCreate(node_id=node.id),
                request=_req(),
            )
        self.assertEqual(created_lease["status"], "leased")

        lease_row.status = "running"
        with patch("app.routers.runtime.get_session", _session([lease_row, job_row])):
            updated = update_lease_status(
                lease_id=lease_row.id,
                body=LeaseStatus(status="running"),
                request=_req(),
            )
        self.assertEqual(updated["status"], "running")

        lease_row.status = "completed"
        with patch("app.routers.runtime.get_session", _session([lease_row, job_row])):
            completed = complete_lease(
                lease_id=lease_row.id,
                body=LeaseComplete(exit_code=0, error_message=""),
                request=_req(),
            )
        self.assertEqual(completed["status"], "completed")


if __name__ == "__main__":
    unittest.main()
