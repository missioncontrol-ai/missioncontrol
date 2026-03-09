import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import Response
from sqlmodel import SQLModel, select

from app.db import engine, get_session
from app.models import Artifact, Doc, Kluster, Mission
from app.routers.mcp import call_tool
from app.schemas import MCPCall


def _request(email: str):
    return SimpleNamespace(
        state=SimpleNamespace(principal={"email": email, "subject": email}),
        headers={},
        url=SimpleNamespace(path="/mcp/call"),
        app=SimpleNamespace(state=SimpleNamespace(schema_pack=None)),
    )


class WorkspaceMcpTests(unittest.TestCase):
    def setUp(self):
        SQLModel.metadata.drop_all(engine)
        SQLModel.metadata.create_all(engine)
        with get_session() as session:
            session.add(Mission(id="mission-a", name="mission-a", owners="owner@example.com"))
            session.add(Kluster(id="kluster-a", mission_id="mission-a", name="kluster-a", owners="owner@example.com"))
            session.add(
                Doc(
                    id=1,
                    kluster_id="kluster-a",
                    title="Readme",
                    body="original body",
                    doc_type="narrative",
                    status="draft",
                    provenance="",
                )
            )
            session.add(
                Artifact(
                    id=1,
                    kluster_id="kluster-a",
                    name="artifact-a",
                    artifact_type="file",
                    uri="s3://missioncontrol-dev/missions/mission-a/klusters/kluster-a/artifacts/artifact-a",
                    storage_backend="s3",
                    status="draft",
                )
            )
            session.commit()
        self.req = _request("owner@example.com")

    def test_load_commit_and_release_workspace(self):
        loaded = call_tool(MCPCall(tool="load_kluster_workspace", args={"kluster_id": "kluster-a"}), self.req, Response())
        self.assertTrue(loaded.ok)
        lease_id = loaded.result["lease"]["id"]
        self.assertIn("NORTHSTAR.md", loaded.result["workspace_snapshot"]["workspace_files"])
        self.assertIn("WORKSTREAM.md", loaded.result["workspace_snapshot"]["workspace_files"])
        docs = loaded.result["workspace_snapshot"]["docs"]
        self.assertTrue(docs)
        doc_path = docs[0]["path"]

        committed = call_tool(
            MCPCall(
                tool="commit_kluster_workspace",
                args={
                    "lease_id": lease_id,
                    "change_set": [{"path": doc_path, "content": "updated via workspace"}],
                },
            ),
            self.req,
            Response(),
        )
        self.assertTrue(committed.ok)
        self.assertEqual(committed.result["applied_count"], 1)

        with get_session() as session:
            doc = session.exec(select(Doc).where(Doc.id == 1)).first()
            self.assertEqual(doc.body, "updated via workspace")

        released = call_tool(
            MCPCall(tool="release_kluster_workspace", args={"lease_id": lease_id, "reason": "done"}),
            self.req,
            Response(),
        )
        self.assertTrue(released.ok)
        self.assertEqual(released.result["lease"]["status"], "released")

    def test_commit_keystone_files(self):
        loaded = call_tool(MCPCall(tool="load_kluster_workspace", args={"kluster_id": "kluster-a"}), self.req, Response())
        self.assertTrue(loaded.ok)
        lease_id = loaded.result["lease"]["id"]

        committed = call_tool(
            MCPCall(
                tool="commit_kluster_workspace",
                args={
                    "lease_id": lease_id,
                    "change_set": [
                        {"path": "NORTHSTAR.md", "content": "# NORTHSTAR: mission-a\n\nupdated mission guidance"},
                        {"path": "WORKSTREAM.md", "content": "# WORKSTREAM: kluster-a\n\nupdated kluster guidance"},
                    ],
                },
            ),
            self.req,
            Response(),
        )
        self.assertTrue(committed.ok)
        self.assertEqual(committed.result["applied_count"], 2)

        with get_session() as session:
            mission = session.get(Mission, "mission-a")
            kluster = session.get(Kluster, "kluster-a")
            self.assertIn("updated mission guidance", mission.northstar_md)
            self.assertGreaterEqual(int(mission.northstar_version or 0), 2)
            self.assertIn("updated kluster guidance", kluster.workstream_md)
            self.assertGreaterEqual(int(kluster.workstream_version or 0), 2)

    def test_commit_conflict_and_readonly_guard(self):
        loaded = call_tool(MCPCall(tool="load_kluster_workspace", args={"kluster_id": "kluster-a"}), self.req, Response())
        lease_id = loaded.result["lease"]["id"]
        doc_path = loaded.result["workspace_snapshot"]["docs"][0]["path"]

        with get_session() as session:
            doc = session.get(Doc, 1)
            doc.body = "changed elsewhere"
            doc.version += 1
            session.add(doc)
            session.commit()

        conflicted = call_tool(
            MCPCall(
                tool="commit_kluster_workspace",
                args={"lease_id": lease_id, "change_set": [{"path": doc_path, "content": "my stale edit"}]},
            ),
            self.req,
            Response(),
        )
        self.assertFalse(conflicted.ok)
        self.assertEqual(conflicted.error, "workspace_conflicts_detected")
        self.assertTrue(conflicted.result["conflicts"])

        readonly = call_tool(
            MCPCall(
                tool="commit_kluster_workspace",
                args={
                    "lease_id": lease_id,
                    "change_set": [{"path": "policies.json", "content": "{\"x\":1}"}],
                },
            ),
            self.req,
            Response(),
        )
        self.assertFalse(readonly.ok)
        self.assertEqual(readonly.error, "workspace_conflicts_detected")
        self.assertEqual(readonly.result["conflicts"][0]["reason"], "readonly_snapshot")

    def test_fetch_workspace_artifact_download_url(self):
        loaded = call_tool(MCPCall(tool="load_kluster_workspace", args={"kluster_id": "kluster-a"}), self.req, Response())
        lease_id = loaded.result["lease"]["id"]
        with patch("app.routers.mcp.presign_get_uri", return_value="https://signed.example/object"):
            fetched = call_tool(
                MCPCall(
                    tool="fetch_workspace_artifact",
                    args={"lease_id": lease_id, "artifact_id": 1, "mode": "download_url", "expires_seconds": 120},
                ),
                self.req,
                Response(),
            )
        self.assertTrue(fetched.ok)
        self.assertEqual(fetched.result["mode"], "download_url")
        self.assertTrue(fetched.result["download_url"].startswith("https://"))


if __name__ == "__main__":
    unittest.main()
