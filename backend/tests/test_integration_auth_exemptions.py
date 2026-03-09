import os
import sys
import types
import unittest

# Minimal module stubs for environments without paho installed.
paho = types.ModuleType("paho")
paho_mqtt = types.ModuleType("paho.mqtt")
paho_mqtt_client = types.ModuleType("paho.mqtt.client")


class _DummyClient:
    def __init__(self, *args, **kwargs):
        pass


paho_mqtt_client.Client = _DummyClient
paho_mqtt.client = paho_mqtt_client
paho.mqtt = paho_mqtt
sys.modules.setdefault("paho", paho)
sys.modules.setdefault("paho.mqtt", paho_mqtt)
sys.modules.setdefault("paho.mqtt.client", paho_mqtt_client)

os.environ.setdefault("AUTH_MODE", "token")
os.environ.setdefault("MC_TOKEN", "test-token")

from app.main import _is_auth_exempt_path


class IntegrationAuthExemptionsTests(unittest.TestCase):
    def test_chat_bindings_not_exempt(self):
        self.assertFalse(_is_auth_exempt_path("/integrations/chat/bindings"))

    def test_slack_bindings_not_exempt(self):
        self.assertFalse(_is_auth_exempt_path("/integrations/slack/bindings"))

    def test_webhook_callbacks_remain_exempt(self):
        self.assertTrue(_is_auth_exempt_path("/integrations/slack/events"))
        self.assertTrue(_is_auth_exempt_path("/integrations/slack/commands"))
        self.assertTrue(_is_auth_exempt_path("/integrations/slack/interactions"))
        self.assertTrue(_is_auth_exempt_path("/integrations/google-chat/events"))
        self.assertTrue(_is_auth_exempt_path("/integrations/teams/events"))


if __name__ == "__main__":
    unittest.main()
