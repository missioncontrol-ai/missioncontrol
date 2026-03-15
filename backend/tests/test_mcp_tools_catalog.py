import unittest

from app.routers.mcp import TOOLS


class McpToolsCatalogTests(unittest.TestCase):
    def test_catalog_includes_doc_and_artifact_create_tools(self):
        names = {tool.name for tool in TOOLS}
        self.assertIn("create_doc", names)
        self.assertIn("create_artifact", names)
        self.assertIn("get_artifact_download_url", names)
        self.assertIn("load_kluster_workspace", names)
        self.assertIn("commit_kluster_workspace", names)
        self.assertIn("release_kluster_workspace", names)
        self.assertIn("list_profiles", names)
        self.assertIn("publish_profile", names)
        self.assertIn("download_profile", names)
        self.assertIn("pin_profile_version", names)


if __name__ == "__main__":
    unittest.main()
