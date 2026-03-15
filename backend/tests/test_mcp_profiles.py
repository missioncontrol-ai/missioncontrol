import io
import tarfile
import base64
import unittest
from types import SimpleNamespace

from fastapi import Response
from sqlmodel import SQLModel

from app.db import engine
from app.routers.mcp import call_tool
from app.schemas import MCPCall


def _request(email: str):
    return SimpleNamespace(
        state=SimpleNamespace(principal={"email": email, "subject": email}),
        headers={},
        url=SimpleNamespace(path="/mcp/call"),
        app=SimpleNamespace(state=SimpleNamespace(schema_pack=None)),
    )


def _make_tarball(files: dict[str, str]) -> str:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for path, text in files.items():
            data = text.encode("utf-8")
            info = tarfile.TarInfo(name=path)
            info.size = len(data)
            info.mtime = 0
            tf.addfile(info, io.BytesIO(data))
    return base64.b64encode(buf.getvalue()).decode("ascii")


class McpProfileToolsTests(unittest.TestCase):
    def setUp(self):
        SQLModel.metadata.drop_all(engine)
        SQLModel.metadata.create_all(engine)
        self.req = _request("owner@example.com")

    def test_publish_list_status_pin_download_and_activate(self):
        tarball = _make_tarball({"SKILL.md": "hello"})
        published = call_tool(
            MCPCall(
                tool="publish_profile",
                args={"name": "research", "description": "r", "is_default": True, "manifest": [], "tarball_b64": tarball},
            ),
            self.req,
            Response(),
        )
        self.assertTrue(published.ok)
        remote_sha = published.result["profile"]["sha256"]
        self.assertTrue(remote_sha)

        listed = call_tool(MCPCall(tool="list_profiles", args={}), self.req, Response())
        self.assertTrue(listed.ok)
        self.assertEqual(len(listed.result["profiles"]), 1)

        status = call_tool(
            MCPCall(tool="profile_status", args={"name": "research", "expected_sha256": remote_sha}),
            self.req,
            Response(),
        )
        self.assertTrue(status.ok)
        self.assertTrue(status.result["matches_expected"])

        pin_ok = call_tool(
            MCPCall(tool="pin_profile_version", args={"name": "research", "sha256": remote_sha}),
            self.req,
            Response(),
        )
        self.assertTrue(pin_ok.ok)
        self.assertTrue(pin_ok.result["matches"])

        downloaded = call_tool(
            MCPCall(tool="download_profile", args={"name": "research", "if_sha256": remote_sha}),
            self.req,
            Response(),
        )
        self.assertTrue(downloaded.ok)
        self.assertEqual(downloaded.result["profile"]["name"], "research")
        self.assertEqual(downloaded.result["tarball_b64"], tarball)

        activated = call_tool(MCPCall(tool="activate_profile", args={"name": "research"}), self.req, Response())
        self.assertTrue(activated.ok)
        self.assertTrue(activated.result["profile"]["is_default"])

    def test_publish_expected_sha_conflict(self):
        tarball = _make_tarball({"SKILL.md": "hello"})
        first = call_tool(
            MCPCall(tool="publish_profile", args={"name": "dev", "tarball_b64": tarball}),
            self.req,
            Response(),
        )
        self.assertTrue(first.ok)
        conflict = call_tool(
            MCPCall(
                tool="publish_profile",
                args={"name": "dev", "tarball_b64": tarball, "expected_sha256": "deadbeef"},
            ),
            self.req,
            Response(),
        )
        self.assertFalse(conflict.ok)
        self.assertEqual(conflict.error, "profile_sha_mismatch")


if __name__ == "__main__":
    unittest.main()
