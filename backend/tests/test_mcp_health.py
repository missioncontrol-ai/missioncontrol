import os
import unittest
from unittest.mock import patch

from app.routers.mcp import mcp_health


class McpHealthTests(unittest.TestCase):
    def test_health_contains_expected_fields(self):
        with patch.dict(
            os.environ,
            {
                "AUTH_MODE": "dual",
                "MC_TOKEN": "token",
                "OIDC_ISSUER_URL": "https://issuer.example",
                "OIDC_AUDIENCE": "missioncontrol",
            },
            clear=False,
        ):
            payload = mcp_health()
        self.assertEqual(payload["status"], "ok")
        self.assertTrue(payload["token_configured"])
        self.assertTrue(payload["oidc_configured"])
        self.assertGreater(payload["tools_count"], 0)
        self.assertEqual(payload["auth_mode"], "dual")


if __name__ == "__main__":
    unittest.main()
