"""
MC-MCP-007: mutation idempotency / task claim guardrails.

Tests:
- claim_task requires task_id
- claim_task returns conflict when task already owned by another agent
- claim_task succeeds when task is proposed
- update_task expected_status returns conflict on mismatch
- update_task expected_status passes through when status matches
- update_task owner guard blocks status change by non-owner
- update_task owner guard allows description update by non-owner
"""
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import Response

from app.routers.mcp import call_tool, list_tools
from app.schemas import MCPCall


def _make_request(subject="agent-a"):
    return SimpleNamespace(
        state=SimpleNamespace(principal={"email": "", "subject": subject}),
        headers={},
        url=SimpleNamespace(path="/mcp/call"),
    )


def _make_task(status="proposed", owner="", public_id="task-001", internal_id=1, kluster_id="kluster-1"):
    task = MagicMock()
    task.id = internal_id
    task.public_id = public_id
    task.kluster_id = kluster_id
    task.status = status
    task.owner = owner
    task.title = "Test task"
    task.description = ""
    task.contributors = ""
    task.dependencies = ""
    task.definition_of_done = ""
    task.related_artifacts = ""
    task.dict.return_value = {
        "id": internal_id,
        "public_id": public_id,
        "kluster_id": kluster_id,
        "status": status,
        "owner": owner,
    }
    return task


def _make_kluster(mission_id="mission-1"):
    kluster = MagicMock()
    kluster.mission_id = mission_id
    return kluster


class McpTaskClaimTests(unittest.TestCase):
    def test_catalog_includes_claim_task(self):
        names = {tool.name for tool in list_tools()}
        self.assertIn("claim_task", names)

    def test_claim_task_requires_task_id(self):
        request = _make_request()

        @contextmanager
        def _dummy_session():
            yield object()

        with patch("app.routers.mcp.get_session", _dummy_session), patch("app.routers.mcp.require_policy_action"):
            response = call_tool(MCPCall(tool="claim_task", args={}), request, Response())
        self.assertFalse(response.ok)
        self.assertEqual((response.result or {}).get("error_code"), "invalid_request")
        self.assertTrue((response.result or {}).get("request_id"))

    def test_claim_task_not_found(self):
        request = _make_request()

        @contextmanager
        def _session():
            yield object()

        with (
            patch("app.routers.mcp.get_session", _session),
            patch("app.routers.mcp.require_policy_action"),
            patch("app.routers.mcp.resolve_task_by_ref", return_value=None),
        ):
            response = call_tool(MCPCall(tool="claim_task", args={"task_id": "missing"}), request, Response())
        self.assertFalse(response.ok)
        self.assertEqual((response.result or {}).get("error_code"), "not_found")

    def test_claim_task_conflict_when_already_owned(self):
        request = _make_request(subject="agent-b")
        task = _make_task(status="in_progress", owner="agent-a")

        @contextmanager
        def _session():
            s = MagicMock()
            s.get.return_value = _make_kluster()
            yield s

        with (
            patch("app.routers.mcp.get_session", _session),
            patch("app.routers.mcp.require_policy_action"),
            patch("app.routers.mcp.resolve_task_by_ref", return_value=task),
            patch("app.routers.mcp.assert_mission_writer_or_admin"),
        ):
            response = call_tool(MCPCall(tool="claim_task", args={"task_id": "task-001"}), request, Response())
        self.assertFalse(response.ok)
        self.assertEqual((response.result or {}).get("error_code"), "conflict")
        self.assertEqual((response.result or {}).get("current_owner"), "agent-a")
        self.assertTrue((response.result or {}).get("request_id"))

    def test_claim_task_succeeds_on_proposed(self):
        request = _make_request(subject="agent-a")
        task = _make_task(status="proposed", owner="")

        @contextmanager
        def _session():
            s = MagicMock()
            s.get.return_value = _make_kluster()
            yield s

        with (
            patch("app.routers.mcp.get_session", _session),
            patch("app.routers.mcp.require_policy_action"),
            patch("app.routers.mcp.resolve_task_by_ref", return_value=task),
            patch("app.routers.mcp.assert_mission_writer_or_admin"),
            patch("app.routers.mcp.enqueue_ledger_event"),
            patch("app.routers.mcp.task_to_public_dict", return_value={"status": "in_progress", "owner": "agent-a"}),
            patch("app.routers.mcp._mutation_result_with_ledger", return_value={"task": {}, "request_id": "r1"}),
        ):
            response = call_tool(MCPCall(tool="claim_task", args={"task_id": "task-001"}), request, Response())
        self.assertTrue(response.ok)
        self.assertTrue((response.result or {}).get("request_id"))


class McpUpdateTaskGuardsTests(unittest.TestCase):
    def test_update_task_expected_status_conflict(self):
        request = _make_request()
        task = _make_task(status="done", owner="agent-a")

        @contextmanager
        def _session():
            s = MagicMock()
            s.get.return_value = _make_kluster()
            yield s

        with (
            patch("app.routers.mcp.get_session", _session),
            patch("app.routers.mcp.require_policy_action"),
            patch("app.routers.mcp.resolve_task_by_ref", return_value=task),
        ):
            response = call_tool(
                MCPCall(tool="update_task", args={"task_id": "task-001", "expected_status": "in_progress", "description": "x"}),
                request,
                Response(),
            )
        self.assertFalse(response.ok)
        self.assertEqual((response.result or {}).get("error_code"), "conflict")
        self.assertEqual((response.result or {}).get("current_status"), "done")
        self.assertEqual((response.result or {}).get("expected_status"), "in_progress")
        self.assertTrue((response.result or {}).get("request_id"))

    def test_update_task_expected_status_matches_proceeds(self):
        request = _make_request(subject="agent-a")
        task = _make_task(status="in_progress", owner="agent-a")

        @contextmanager
        def _session():
            s = MagicMock()
            s.get.return_value = _make_kluster()
            yield s

        with (
            patch("app.routers.mcp.get_session", _session),
            patch("app.routers.mcp.require_policy_action"),
            patch("app.routers.mcp.resolve_task_by_ref", return_value=task),
            patch("app.routers.mcp.assert_mission_writer_or_admin"),
            patch("app.routers.mcp.enforce_schema_pack", side_effect=lambda **kw: kw["payload"]),
            patch("app.routers.mcp.index_task"),
            patch("app.routers.mcp.enqueue_ledger_event"),
            patch("app.routers.mcp.task_to_public_dict", return_value={}),
            patch("app.routers.mcp._mutation_result_with_ledger", return_value={"task": {}, "request_id": "r1"}),
        ):
            response = call_tool(
                MCPCall(tool="update_task", args={"task_id": "task-001", "expected_status": "in_progress", "description": "updated"}),
                request,
                Response(),
            )
        self.assertTrue(response.ok)

    def test_update_task_owner_guard_blocks_status_change(self):
        request = _make_request(subject="agent-b")
        task = _make_task(status="in_progress", owner="agent-a")

        @contextmanager
        def _session():
            s = MagicMock()
            s.get.return_value = _make_kluster()
            yield s

        with (
            patch("app.routers.mcp.get_session", _session),
            patch("app.routers.mcp.require_policy_action"),
            patch("app.routers.mcp.resolve_task_by_ref", return_value=task),
            patch("app.routers.mcp.assert_mission_writer_or_admin"),
            patch("app.routers.mcp.is_platform_admin", return_value=False),
        ):
            response = call_tool(
                MCPCall(tool="update_task", args={"task_id": "task-001", "status": "done"}),
                request,
                Response(),
            )
        self.assertFalse(response.ok)
        self.assertEqual((response.result or {}).get("error_code"), "forbidden")
        self.assertEqual((response.result or {}).get("current_owner"), "agent-a")
        self.assertTrue((response.result or {}).get("request_id"))

    def test_update_task_owner_guard_allows_description_update(self):
        """Non-owner can update description but not status."""
        request = _make_request(subject="agent-b")
        task = _make_task(status="in_progress", owner="agent-a")

        @contextmanager
        def _session():
            s = MagicMock()
            s.get.return_value = _make_kluster()
            yield s

        with (
            patch("app.routers.mcp.get_session", _session),
            patch("app.routers.mcp.require_policy_action"),
            patch("app.routers.mcp.resolve_task_by_ref", return_value=task),
            patch("app.routers.mcp.assert_mission_writer_or_admin"),
            patch("app.routers.mcp.is_platform_admin", return_value=False),
            patch("app.routers.mcp.enforce_schema_pack", side_effect=lambda **kw: kw["payload"]),
            patch("app.routers.mcp.index_task"),
            patch("app.routers.mcp.enqueue_ledger_event"),
            patch("app.routers.mcp.task_to_public_dict", return_value={}),
            patch("app.routers.mcp._mutation_result_with_ledger", return_value={"task": {}, "request_id": "r1"}),
        ):
            response = call_tool(
                MCPCall(tool="update_task", args={"task_id": "task-001", "description": "added findings"}),
                request,
                Response(),
            )
        self.assertTrue(response.ok)

    def test_update_task_platform_admin_can_override_owner_guard(self):
        request = _make_request(subject="admin")
        task = _make_task(status="in_progress", owner="agent-a")

        @contextmanager
        def _session():
            s = MagicMock()
            s.get.return_value = _make_kluster()
            yield s

        with (
            patch("app.routers.mcp.get_session", _session),
            patch("app.routers.mcp.require_policy_action"),
            patch("app.routers.mcp.resolve_task_by_ref", return_value=task),
            patch("app.routers.mcp.assert_mission_writer_or_admin"),
            patch("app.routers.mcp.is_platform_admin", return_value=True),
            patch("app.routers.mcp.enforce_schema_pack", side_effect=lambda **kw: kw["payload"]),
            patch("app.routers.mcp.index_task"),
            patch("app.routers.mcp.enqueue_ledger_event"),
            patch("app.routers.mcp.task_to_public_dict", return_value={}),
            patch("app.routers.mcp._mutation_result_with_ledger", return_value={"task": {}, "request_id": "r1"}),
        ):
            response = call_tool(
                MCPCall(tool="update_task", args={"task_id": "task-001", "status": "done"}),
                request,
                Response(),
            )
        self.assertTrue(response.ok)


if __name__ == "__main__":
    unittest.main()
