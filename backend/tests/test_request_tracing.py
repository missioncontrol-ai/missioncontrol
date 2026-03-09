import os
import sys
import types
import unittest
from types import SimpleNamespace

from fastapi.responses import JSONResponse

# Minimal stubs for environments without paho installed.
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

os.environ["AUTH_MODE"] = "token"
os.environ["MC_TOKEN"] = "test-token"
os.environ["OIDC_ISSUER_URL"] = ""
os.environ["OIDC_AUDIENCE"] = ""

from app.main import _extract_mission_id, _finalize_response, _is_auth_exempt_path, _request_id


class RequestTracingTests(unittest.TestCase):
    def _request(self, *, path: str = "/", mission_id: str | None = None):
        path_params = {}
        if mission_id:
            path_params["mission_id"] = mission_id
        return SimpleNamespace(
            method="GET",
            path_params=path_params,
            query_params={},
            url=SimpleNamespace(path=path),
            scope={"route": SimpleNamespace(path=path)},
            state=SimpleNamespace(),
        )

    def test_request_id_generated_when_missing(self):
        request = self._request()
        value = _request_id(request)
        self.assertTrue(value)
        self.assertEqual(request.state.request_id, value)

    def test_request_id_reused_when_present(self):
        request = self._request()
        request.state.request_id = "req-abc"
        self.assertEqual(_request_id(request), "req-abc")

    def test_extract_mission_id_from_path_params(self):
        request = self._request(path="/missions/m-1/k", mission_id="m-1")
        self.assertEqual(_extract_mission_id(request), "m-1")

    def test_finalize_response_sets_request_id_header(self):
        request = self._request()
        request.state.request_id = "req-1"
        response = _finalize_response(request, JSONResponse(status_code=200, content={"ok": True}), 0.0)
        self.assertEqual(response.headers.get("x-request-id"), "req-1")

    def test_auth_exempt_webhook_paths_still_exempt(self):
        self.assertTrue(_is_auth_exempt_path("/integrations/slack/events"))
        self.assertTrue(_is_auth_exempt_path("/integrations/google-chat/events"))


if __name__ == "__main__":
    unittest.main()
