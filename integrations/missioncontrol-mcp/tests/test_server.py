import os
import sys
import types
import unittest
import tempfile
import tarfile
import io
from unittest.mock import patch

if "paho.mqtt.client" not in sys.modules:
    paho_mod = types.ModuleType("paho")
    mqtt_mod = types.ModuleType("paho.mqtt")
    client_mod = types.ModuleType("paho.mqtt.client")

    class _DummyClient:
        def __init__(self, *args, **kwargs):
            pass

        def username_pw_set(self, *args, **kwargs):
            pass

        def connect(self, *args, **kwargs):
            pass

        def loop_start(self):
            pass

        def loop_stop(self):
            pass

        def disconnect(self):
            pass

    client_mod.Client = _DummyClient
    mqtt_mod.client = client_mod
    paho_mod.mqtt = mqtt_mod
    sys.modules["paho"] = paho_mod
    sys.modules["paho.mqtt"] = mqtt_mod
    sys.modules["paho.mqtt.client"] = client_mod

from missioncontrol_mcp import server


class ServerConfigTests(unittest.TestCase):
    def test_base_urls_uses_fallback_list(self):
        with patch.dict(
            os.environ,
            {
                "MC_BASE_URLS": "https://a.example, https://b.example ,http://localhost:8008",
                "MC_BASE_URL": "https://ignored.example",
            },
            clear=False,
        ):
            self.assertEqual(
                server.base_urls(),
                ["https://a.example", "https://b.example", "http://localhost:8008"],
            )

    def test_base_urls_falls_back_to_single_base_url(self):
        with patch.dict(
            os.environ,
            {"MC_BASE_URLS": "", "MC_BASE_URL": "https://single.example"},
            clear=False,
        ):
            self.assertEqual(server.base_urls(), ["https://single.example"])

    def test_preflight_mode_defaults_on_invalid(self):
        with patch.dict(os.environ, {"MC_STARTUP_PREFLIGHT": "invalid"}, clear=False):
            self.assertEqual(server._preflight_mode(), "health")

    def test_parse_bool_env(self):
        with patch.dict(os.environ, {"MC_FAIL_OPEN_ON_LIST": "true"}, clear=False):
            self.assertTrue(server.parse_bool_env("MC_FAIL_OPEN_ON_LIST", False))
        with patch.dict(os.environ, {"MC_FAIL_OPEN_ON_LIST": "0"}, clear=False):
            self.assertFalse(server.parse_bool_env("MC_FAIL_OPEN_ON_LIST", True))

    def test_daemon_base_url_defaults(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(server._daemon_base_url(), "http://127.0.0.1:8765")

    def test_auth_mode_auto_prefers_oidc_when_configured(self):
        with patch.dict(
            os.environ,
            {
                "MC_AUTH_MODE": "auto",
                "MC_OIDC_TOKEN_URL": "https://idp.example/token",
                "MC_OIDC_CLIENT_ID": "client-a",
                "MC_OIDC_CLIENT_SECRET": "secret-a",
                "MC_TOKEN": "fallback-token",
            },
            clear=False,
        ):
            client = server.MissionControlHttpClient()
            self.assertEqual(client._auth_mode_effective(), "oidc")

    def test_auth_mode_auto_falls_back_to_token(self):
        with patch.dict(
            os.environ,
            {
                "MC_AUTH_MODE": "auto",
                "MC_OIDC_TOKEN_URL": "",
                "MC_OIDC_CLIENT_ID": "",
                "MC_OIDC_CLIENT_SECRET": "",
                "MC_TOKEN": "fallback-token",
            },
            clear=False,
        ):
            client = server.MissionControlHttpClient()
            self.assertEqual(client._auth_mode_effective(), "token")

    def test_circuit_breaker_opens_after_failures(self):
        br = server._CircuitBreaker(
            name="x",
            window_sec=20,
            min_requests=10,
            failure_rate_open=0.5,
            consecutive_failures_open=2,
            half_open_probe_sec=1,
        )
        self.assertTrue(br.allow())
        br.record(False)
        self.assertTrue(br.allow())
        br.record(False)
        self.assertFalse(br.allow())

    def test_http_json_helper_uses_client(self):
        class _Client:
            def http_json(self, method, path, payload=None):
                return {"method": method, "path": path, "payload": payload}

        with patch.object(server, "_HTTP_CLIENT_SINGLETON", _Client()):
            out = server.http_json("GET", "/explorer/tree")
        self.assertEqual(out["method"], "GET")
        self.assertEqual(out["path"], "/explorer/tree")

    def test_signing_payload_and_signature_are_deterministic(self):
        with tempfile.TemporaryDirectory() as td:
            bundle_path = os.path.join(td, "bundle.tar.gz")
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w:gz") as tf:
                data = b"mission-skill"
                info = tarfile.TarInfo(name="SKILL.md")
                info.size = len(data)
                info.mode = 0o644
                info.mtime = 0
                tf.addfile(info, io.BytesIO(data))
            with open(bundle_path, "wb") as f:
                f.write(buf.getvalue())

            entries = server._extract_bundle_entries_from_path(server.Path(bundle_path))
            manifest = server._normalized_bundle_manifest_for_signing(
                scope_type="mission",
                scope_id="mission-a",
                mission_id="mission-a",
                kluster_id="",
                manifest_payload={},
                entries=entries,
            )
            tar_sha = server._bundle_sha256_from_path(server.Path(bundle_path))
            payload = server._bundle_signature_payload(manifest, tar_sha, "hmac-sha256")
            sig1 = server.hmac.new(b"secret-1", payload.encode("utf-8"), server.hashlib.sha256).hexdigest()
            sig2 = server.hmac.new(b"secret-1", payload.encode("utf-8"), server.hashlib.sha256).hexdigest()
            self.assertEqual(sig1, sig2)


if __name__ == "__main__":
    unittest.main()
