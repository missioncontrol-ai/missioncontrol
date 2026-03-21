"""Tests for MCP remote agent tools."""
import unittest
from types import SimpleNamespace
from sqlmodel import SQLModel

from app.db import engine
from app.routers.mcp import TOOLS, call_tool
from app.schemas import MCPCall

SUBJECT = "agent@example.com"

EXPECTED_REMOTE_TOOLS = {
    "register_remote_target", "list_remote_targets", "delete_remote_target",
    "create_remote_launch", "list_remote_launches", "get_remote_launch", "kill_remote_launch",
}


def _req(subject=SUBJECT):
    return SimpleNamespace(
        state=SimpleNamespace(
            principal={"subject": subject, "email": subject},
            request_id=None,
            mcp_tool=None,
        ),
        headers={},
        url=SimpleNamespace(path="/mcp/call"),
    )


def _call(tool, args, subject=SUBJECT):
    payload = MCPCall(tool=tool, args=args)
    req = _req(subject)

    class FakeResponse:
        headers = {}

    return call_tool(payload=payload, request=req, response=FakeResponse())


class TestMCPRemoteToolsPresent(unittest.TestCase):
    def test_remote_tools_in_tools_list(self):
        names = {t.name for t in TOOLS}
        for tool_name in EXPECTED_REMOTE_TOOLS:
            self.assertIn(tool_name, names, f"Missing MCP tool: {tool_name}")


class TestMCPRemoteDispatch(unittest.TestCase):
    def setUp(self):
        SQLModel.metadata.drop_all(engine, checkfirst=True)
        SQLModel.metadata.create_all(engine)

    def test_register_and_list_target(self):
        result = _call("register_remote_target", {
            "name": "test-box", "host": "10.0.0.1", "transport": "ssh",
        })
        self.assertTrue(result.ok)
        listed = _call("list_remote_targets", {})
        self.assertTrue(listed.ok)
        self.assertEqual(len(listed.result["targets"]), 1)

    def test_create_launch_token(self):
        t = _call("register_remote_target",
                  {"name": "box", "host": "10.0.0.1", "transport": "ssh"})
        launch = _call("create_remote_launch", {
            "transport": "ssh", "target_id": t.result["id"], "agent_kind": "claude",
        })
        self.assertTrue(launch.ok)
        self.assertIn("session_token", launch.result)
        self.assertIn("launch_id", launch.result)
        self.assertIn("mc_base_url", launch.result)

    def test_get_and_kill_launch(self):
        t = _call("register_remote_target",
                  {"name": "box2", "host": "10.0.0.1", "transport": "ssh"})
        launch = _call("create_remote_launch",
                       {"transport": "ssh", "target_id": t.result["id"], "agent_kind": "claude"})
        launch_id = launch.result["launch_id"]
        status = _call("get_remote_launch", {"launch_id": launch_id})
        self.assertEqual(status.result["status"], "launching")
        _call("kill_remote_launch", {"launch_id": launch_id})
        status = _call("get_remote_launch", {"launch_id": launch_id})
        self.assertEqual(status.result["status"], "failed")

    def test_delete_target(self):
        t = _call("register_remote_target",
                  {"name": "box3", "host": "10.0.0.1", "transport": "ssh"})
        _call("delete_remote_target", {"target_id": t.result["id"]})
        listed = _call("list_remote_targets", {})
        self.assertEqual(len(listed.result["targets"]), 0)

    def test_list_launches_filter(self):
        t = _call("register_remote_target",
                  {"name": "box4", "host": "10.0.0.1", "transport": "ssh"})
        _call("create_remote_launch",
              {"transport": "ssh", "target_id": t.result["id"], "agent_kind": "claude"})
        all_launches = _call("list_remote_launches", {})
        self.assertTrue(all_launches.ok)
        filtered = _call("list_remote_launches", {"status": "launching"})
        self.assertTrue(len(filtered.result["launches"]) >= 1)
        none = _call("list_remote_launches", {"status": "completed"})
        self.assertEqual(len(none.result["launches"]), 0)
