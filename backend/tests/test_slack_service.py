import hashlib
import hmac
import os
import time
import unittest
from unittest.mock import patch

from app.services.slack import command_mission_id, verify_slack_signature


class SlackServiceTests(unittest.TestCase):
    def test_verify_slack_signature_accepts_valid_signature(self):
        body = b"command=%2Fmc&text=mission_id%3Dm-1"
        ts = str(int(time.time()))
        with patch.dict(os.environ, {"SLACK_SIGNING_SECRET": "test-secret"}, clear=False):
            base = f"v0:{ts}:{body.decode('utf-8')}".encode("utf-8")
            sig = "v0=" + hmac.new(b"test-secret", base, hashlib.sha256).hexdigest()
            ok, reason = verify_slack_signature(
                headers={
                    "X-Slack-Request-Timestamp": ts,
                    "X-Slack-Signature": sig,
                },
                body=body,
            )
            self.assertTrue(ok)
            self.assertEqual(reason, "ok")

    def test_verify_slack_signature_rejects_bad_signature(self):
        body = b"command=%2Fmc&text=test"
        ts = str(int(time.time()))
        with patch.dict(os.environ, {"SLACK_SIGNING_SECRET": "test-secret"}, clear=False):
            ok, reason = verify_slack_signature(
                headers={
                    "X-Slack-Request-Timestamp": ts,
                    "X-Slack-Signature": "v0=deadbeef",
                },
                body=body,
            )
            self.assertFalse(ok)
            self.assertEqual(reason, "slack_signature_invalid")

    def test_command_mission_id_parsing(self):
        self.assertEqual(command_mission_id("run mission_id=m-1"), "m-1")
        self.assertEqual(command_mission_id("ignored", explicit_mission_id="m-2"), "m-2")
        self.assertIsNone(command_mission_id("no mission id"))


if __name__ == "__main__":
    unittest.main()
