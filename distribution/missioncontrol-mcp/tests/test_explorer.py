import unittest


class ExplorerCompatibilityTests(unittest.TestCase):
    def test_import_explorer_module(self):
        # Regression guard: explorer used to import a missing symbol from server.
        from missioncontrol_mcp import explorer  # noqa: F401

    def test_render_markdown_accepts_kluster_keys(self):
        from missioncontrol_mcp.explorer import _render_tree_markdown

        payload = {
            "mission_count": 1,
            "kluster_count": 1,
            "task_count": 0,
            "missions": [
                {
                    "id": "m1",
                    "name": "Mission A",
                    "klusters": [
                        {
                            "id": "k1",
                            "name": "K1",
                            "task_count": 0,
                            "recent_tasks": [],
                        }
                    ],
                }
            ],
            "unassigned_klusters": [],
        }
        rendered = _render_tree_markdown(payload)
        self.assertIn("Mission A", rendered)
        self.assertIn("K1", rendered)
        self.assertIn("1 clusters", rendered)


if __name__ == "__main__":
    unittest.main()
