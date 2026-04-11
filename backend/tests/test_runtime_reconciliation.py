"""Tests for meshagent.runtime_node_id FK — runtime fabric reconciliation (Task 14).

Verifies:
  - Enrolling a MeshAgent with a valid, owned RuntimeNode stores the FK.
  - Enrolling without runtime_node_id still works (nullable, backward compat).
  - Enrolling with an unknown node ID returns 400.
  - Enrolling with a node owned by a different subject returns 400.
"""

import uuid
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

from sqlmodel import Session, SQLModel, create_engine, select

import app.routers.runtime as runtime
import app.routers.work as work
from app.models import MeshAgent, RuntimeNode


SUBJECT = "owner@example.com"
OTHER_SUBJECT = "other@example.com"


def _req(subject: str = SUBJECT):
    return SimpleNamespace(
        state=SimpleNamespace(principal={"subject": subject, "email": subject})
    )


def _bundle_req(subject: str = SUBJECT):
    req = _req(subject)
    req.base_url = "http://testserver/"
    return req


class RuntimeReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        SQLModel.metadata.drop_all(self.engine, checkfirst=True)
        SQLModel.metadata.create_all(self.engine)

    def _session_scope(self, subject: str = SUBJECT):
        engine = self.engine

        @contextmanager
        def _ctx():
            with Session(engine) as session:
                yield session

        return _ctx

    def _register_node(self, subject: str = SUBJECT, node_name: str = "node-a") -> str:
        """Register a RuntimeNode via the runtime router, return node id."""
        with patch.object(runtime, "get_session", self._session_scope(subject)), \
             patch.object(runtime, "actor_subject_from_request", lambda _: subject):
            token_resp = runtime.create_join_token(
                runtime.JoinTokenCreate(expires_in_seconds=300),
                _bundle_req(subject),
            )
            node_resp = runtime.register_node(
                runtime.NodeRegister(
                    node_name=node_name,
                    hostname=f"{node_name}.local",
                    trust_tier="trusted",
                    bootstrap_token=token_resp["join_token"],
                ),
                _req(subject),
            )
        return node_resp["id"]

    def _enroll_agent(self, *, subject: str = SUBJECT, runtime_node_id=None, mission_id: str = "mission-1"):
        with patch.object(work, "get_session", self._session_scope(subject)), \
             patch.object(work, "actor_subject_from_request", lambda _: subject):
            return work.enroll_agent(
                mission_id,
                work.MeshAgentEnroll(
                    runtime_kind="claude_code",
                    runtime_node_id=runtime_node_id,
                ),
                _req(subject),
            )

    # ------------------------------------------------------------------

    def test_enroll_agent_with_valid_runtime_node_id(self):
        node_id = self._register_node()
        result = self._enroll_agent(runtime_node_id=node_id)
        # Verify response contains agent id
        agent_id = result["id"]
        self.assertIsNotNone(agent_id)
        # Verify the FK was stored on the row
        with Session(self.engine) as session:
            agent = session.get(MeshAgent, agent_id)
            self.assertEqual(agent.runtime_node_id, node_id)

    def test_enroll_agent_without_runtime_node_id_works(self):
        result = self._enroll_agent(runtime_node_id=None)
        agent_id = result["id"]
        self.assertIsNotNone(agent_id)
        with Session(self.engine) as session:
            agent = session.get(MeshAgent, agent_id)
            self.assertIsNone(agent.runtime_node_id)

    def test_enroll_agent_with_invalid_runtime_node_id_returns_400(self):
        fake_id = str(uuid.uuid4())
        with patch.object(work, "get_session", self._session_scope()), \
             patch.object(work, "actor_subject_from_request", lambda _: SUBJECT):
            with self.assertRaises(work.HTTPException) as ctx:
                work.enroll_agent(
                    "mission-1",
                    work.MeshAgentEnroll(
                        runtime_kind="claude_code",
                        runtime_node_id=fake_id,
                    ),
                    _req(),
                )
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("not found or not owned", ctx.exception.detail)

    def test_enroll_agent_with_other_owners_node_returns_400(self):
        # Register a node owned by OTHER_SUBJECT
        node_id = self._register_node(subject=OTHER_SUBJECT, node_name="other-node")
        # Try to enroll as SUBJECT using other's node
        with patch.object(work, "get_session", self._session_scope(SUBJECT)), \
             patch.object(work, "actor_subject_from_request", lambda _: SUBJECT):
            with self.assertRaises(work.HTTPException) as ctx:
                work.enroll_agent(
                    "mission-1",
                    work.MeshAgentEnroll(
                        runtime_kind="claude_code",
                        runtime_node_id=node_id,
                    ),
                    _req(SUBJECT),
                )
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("not found or not owned", ctx.exception.detail)


if __name__ == "__main__":
    unittest.main()
