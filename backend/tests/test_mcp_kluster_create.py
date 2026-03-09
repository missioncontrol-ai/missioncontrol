import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import Response

from app.routers.mcp import call_tool, list_tools
from app.schemas import MCPCall


class McpKlusterCreateTests(unittest.TestCase):
    def test_tools_catalog_includes_create_kluster(self):
        tools = list_tools()
        tool_names = {tool.name for tool in tools}
        self.assertIn("create_kluster", tool_names)
        self.assertIn("create_mission", tool_names)
        self.assertIn("list_missions", tool_names)

    def test_create_kluster_requires_mission_id(self):
        request = SimpleNamespace(
            state=SimpleNamespace(principal={"email": "owner@example.com", "subject": "owner-subject"}),
            headers={},
            url=SimpleNamespace(path="/mcp/call"),
        )

        @contextmanager
        def _dummy_session():
            yield object()

        with patch("app.routers.mcp.get_session", _dummy_session), patch("app.routers.mcp.require_policy_action"):
            response = call_tool(MCPCall(tool="create_kluster", args={"name": "x"}), request, Response())
        self.assertFalse(response.ok)
        self.assertEqual(response.error, "mission_id is required")

    def test_create_mission_requires_name(self):
        request = SimpleNamespace(
            state=SimpleNamespace(principal={"email": "owner@example.com", "subject": "owner-subject"}),
            headers={},
            url=SimpleNamespace(path="/mcp/call"),
        )

        @contextmanager
        def _dummy_session():
            yield object()

        with patch("app.routers.mcp.get_session", _dummy_session), patch("app.routers.mcp.require_policy_action"):
            response = call_tool(MCPCall(tool="create_mission", args={}), request, Response())
        self.assertFalse(response.ok)
        self.assertEqual(response.error, "name is required")

    def test_create_mission_requires_owner_when_service_token_actor(self):
        request = SimpleNamespace(
            state=SimpleNamespace(principal={"email": "", "subject": "service-token"}),
            headers={},
            url=SimpleNamespace(path="/mcp/call"),
        )

        @contextmanager
        def _dummy_session():
            yield object()

        with patch("app.routers.mcp.get_session", _dummy_session), patch("app.routers.mcp.require_policy_action"):
            response = call_tool(MCPCall(tool="create_mission", args={"name": "m1"}), request, Response())
        self.assertFalse(response.ok)
        self.assertEqual(response.error, "owners must include at least one owner")

    def test_create_kluster_requires_owner(self):
        request = SimpleNamespace(
            state=SimpleNamespace(principal={"email": "owner@example.com", "subject": "owner-subject"}),
            headers={},
            url=SimpleNamespace(path="/mcp/call"),
        )

        @contextmanager
        def _dummy_session():
            yield object()

        with patch("app.routers.mcp.get_session", _dummy_session), patch("app.routers.mcp.require_policy_action"):
            response = call_tool(
                MCPCall(tool="create_kluster", args={"mission_id": "m1", "name": "k1", "owners": "   "}),
                request,
                Response(),
            )
        self.assertFalse(response.ok)
        self.assertEqual(response.error, "owners must include at least one owner")


if __name__ == "__main__":
    unittest.main()
