import unittest

from app.routers.onboarding import build_agent_onboarding_manifest


class OnboardingManifestTests(unittest.TestCase):
    def test_manifest_contains_expected_endpoints_and_server_config(self):
        manifest = build_agent_onboarding_manifest("mc.example.com")
        self.assertEqual(manifest["endpoints"]["ui"], "https://mc.example.com/ui/")
        self.assertEqual(manifest["endpoints"]["mcp_health"], "https://mc.example.com/mcp/health")
        self.assertEqual(
            manifest["mcp_server"]["env"]["MC_BASE_URL"],
            "https://mc.example.com",
        )
        self.assertEqual(manifest["mcp_server"]["command"], "mc")
        self.assertEqual(manifest["mcp_server"]["args"], ["serve"])
        self.assertNotIn("legacy_mcp_server", manifest)
        self.assertEqual(manifest["mcp_defaults"]["startup_timeout_sec"], 45)
        self.assertIn("MC_BASE_URL", manifest["mcp_server"]["env"])
        self.assertIn("mc-integration", manifest["bootstrap"]["remote_script"])
        self.assertIn("config_generator_script", manifest["automation"])
        self.assertIn("claude_code", manifest["agent_configs"])
        self.assertIn("codex", manifest["agent_configs"])
        self.assertIn("openclaw_nanoclaw", manifest["agent_configs"])
        self.assertEqual(manifest["integration_contract_version"], "1.1.0")

    def test_manifest_normalizes_full_url(self):
        manifest = build_agent_onboarding_manifest("https://mc.example.com/path?x=1")
        self.assertEqual(manifest["generated_for_base_url"], "https://mc.example.com")


if __name__ == "__main__":
    unittest.main()
