import unittest

from app.routers.mcp import TOOLS


class McpToolsCatalogTests(unittest.TestCase):
    def test_catalog_includes_doc_and_artifact_create_tools(self):
        names = {tool.name for tool in TOOLS}
        self.assertIn("create_doc", names)
        self.assertIn("create_artifact", names)
        self.assertIn("claim_task", names)
        self.assertIn("get_artifact_download_url", names)
        self.assertIn("load_kluster_workspace", names)
        self.assertIn("commit_kluster_workspace", names)
        self.assertIn("release_kluster_workspace", names)
        self.assertIn("list_profiles", names)
        self.assertIn("publish_profile", names)
        self.assertIn("download_profile", names)
        self.assertIn("pin_profile_version", names)
        self.assertIn("list_repo_bindings", names)
        self.assertIn("provision_mission_persistence", names)
        self.assertIn("resolve_publish_plan", names)
        self.assertIn("get_publication_status", names)
        # Mesh work model tools
        self.assertIn("submit_mesh_task", names)
        self.assertIn("list_mesh_tasks", names)
        self.assertIn("get_mesh_task", names)
        self.assertIn("claim_mesh_task", names)
        self.assertIn("heartbeat_mesh_task", names)
        self.assertIn("progress_mesh_task", names)
        self.assertIn("complete_mesh_task", names)
        self.assertIn("fail_mesh_task", names)
        self.assertIn("block_mesh_task", names)
        self.assertIn("unblock_mesh_task", names)
        self.assertIn("cancel_mesh_task", names)
        self.assertIn("retry_mesh_task", names)
        self.assertIn("enroll_mesh_agent", names)
        self.assertIn("list_mesh_agents", names)
        self.assertIn("send_mesh_message", names)
        self.assertIn("list_mesh_messages", names)


if __name__ == "__main__":
    unittest.main()
