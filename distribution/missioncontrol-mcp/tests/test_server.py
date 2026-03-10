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


class ProfileStoreTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._patcher = patch.dict(os.environ, {"MC_HOME": self._tmpdir}, clear=False)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        import shutil as _shutil
        _shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _make_store(self):
        return server.ProfileStore()

    def test_state_file_roundtrip(self):
        store = self._make_store()
        self.assertIsNone(store.get_last_profile())
        store.set_last_profile("work")
        self.assertEqual(store.get_last_profile(), "work")

    def test_profile_meta_roundtrip(self):
        store = self._make_store()
        self.assertEqual(store.get_profile_meta("work"), {})
        store.set_profile_meta("work", "abc123", "2026-03-09T00:00:00")
        meta = store.get_profile_meta("work")
        self.assertEqual(meta["sha256"], "abc123")
        self.assertEqual(meta["last_sync_at"], "2026-03-09T00:00:00")

    def test_profile_dir_returns_path(self):
        store = self._make_store()
        d = store.profile_dir("work")
        self.assertTrue(str(d).endswith("work"))

    def test_active_link_not_symlink_initially(self):
        store = self._make_store()
        self.assertIsNone(store.resolve_active_symlink_name())

    def test_resolve_active_name_env_override(self):
        store = self._make_store()
        store.set_last_profile("research")
        with patch.dict(os.environ, {"MC_PROFILE": "work"}, clear=False):
            self.assertEqual(server._resolve_profile_name(store), "work")

    def test_resolve_active_name_falls_back_to_state(self):
        store = self._make_store()
        store.set_last_profile("research")
        with patch.dict(os.environ, {"MC_PROFILE": ""}, clear=False):
            self.assertEqual(server._resolve_profile_name(store), "research")

    def test_resolve_active_name_none_when_not_set(self):
        store = self._make_store()
        with patch.dict(os.environ, {"MC_PROFILE": ""}, clear=False):
            self.assertIsNone(server._resolve_profile_name(store))


class ProfileSyncManagerTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._patcher = patch.dict(os.environ, {"MC_HOME": self._tmpdir}, clear=False)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        import shutil as _shutil
        _shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _make_tarball_b64(self, files: dict) -> str:
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            for path, text in files.items():
                data = text.encode("utf-8") if isinstance(text, str) else text
                info = tarfile.TarInfo(name=path)
                info.size = len(data)
                info.mtime = 0
                tf.addfile(info, io.BytesIO(data))
        import base64
        return base64.b64encode(buf.getvalue()).decode("ascii")

    def _make_sync_mgr(self, downloaded: dict):
        class _FakeHttp:
            def http_json(self, method, path, payload=None):
                return downloaded

        store = server.ProfileStore()
        http = _FakeHttp()
        return store, server.ProfileSyncManager(store, http)

    def test_sync_extracts_files(self):
        import hashlib, base64
        tb = self._make_tarball_b64({"claude.md": "hello"})
        sha256 = hashlib.sha256(base64.b64decode(tb)).hexdigest()
        store, mgr = self._make_sync_mgr({"sha256": sha256, "tarball_b64": tb})
        result = mgr.sync("work")
        self.assertTrue(result["ok"])
        self.assertTrue(result["changed"])
        self.assertTrue((server.Path(self._tmpdir) / "profiles" / "work" / "claude.md").exists())

    def test_sync_skips_when_sha_matches(self):
        import hashlib, base64
        tb = self._make_tarball_b64({"claude.md": "hello"})
        sha256 = hashlib.sha256(base64.b64decode(tb)).hexdigest()
        store, mgr = self._make_sync_mgr({"sha256": sha256, "tarball_b64": tb})
        mgr.sync("work")
        # second sync without force
        result = mgr.sync("work")
        self.assertFalse(result.get("changed"))

    def test_sync_force_re_downloads(self):
        import hashlib, base64
        tb = self._make_tarball_b64({"claude.md": "hello"})
        sha256 = hashlib.sha256(base64.b64decode(tb)).hexdigest()
        store, mgr = self._make_sync_mgr({"sha256": sha256, "tarball_b64": tb})
        mgr.sync("work")
        result = mgr.sync("work", force=True)
        self.assertTrue(result["changed"])

    def test_sync_updates_active_symlink(self):
        import hashlib, base64
        tb = self._make_tarball_b64({"soul.md": "values"})
        sha256 = hashlib.sha256(base64.b64decode(tb)).hexdigest()
        store, mgr = self._make_sync_mgr({"sha256": sha256, "tarball_b64": tb})
        mgr.sync("work")
        active_link = store.active_link()
        self.assertTrue(active_link.is_symlink())
        self.assertEqual(store.resolve_active_symlink_name(), "work")

    def test_sync_updates_state_json(self):
        import hashlib, base64
        tb = self._make_tarball_b64({"claude.md": "ctx"})
        sha256 = hashlib.sha256(base64.b64decode(tb)).hexdigest()
        store, mgr = self._make_sync_mgr({"sha256": sha256, "tarball_b64": tb})
        mgr.sync("research")
        self.assertEqual(store.get_last_profile(), "research")
        meta = store.get_profile_meta("research")
        self.assertEqual(meta["sha256"], sha256)


class ProfileToolHandlingTests(unittest.TestCase):
    """Test profile tool interception in handle_request."""

    def _make_http(self, response):
        class _FakeHttp:
            preferred_base_url = "http://localhost:8008"
            base_url_candidates = ["http://localhost:8008"]

            def http_json(self, method, path, payload=None):
                return response

        return _FakeHttp()

    def test_profile_tools_appear_in_tools_list(self):
        http = self._make_http([])
        msg = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        resp = server.handle_request(
            msg,
            http=http,
            daemon=None,
            preflight_state={"done": True},
            fail_open_on_list=False,
        )
        tool_names = {t["name"] for t in resp["result"]["tools"]}
        self.assertIn("list_profiles", tool_names)
        self.assertIn("switch_profile", tool_names)
        self.assertIn("sync_profile", tool_names)

    def test_profile_tool_not_forwarded_to_backend(self):
        calls = []

        class _FakeHttp:
            preferred_base_url = "http://localhost:8008"
            base_url_candidates = ["http://localhost:8008"]

            def http_json(self, method, path, payload=None):
                calls.append((method, path))
                return []

        import tempfile
        with tempfile.TemporaryDirectory() as td:
            with patch.dict(os.environ, {"MC_HOME": td}, clear=False):
                store = server.ProfileStore()

                class _FakeSyncMgr:
                    def sync(self, name, force=False):
                        return {"ok": True, "changed": True, "sha256": "abc"}

                http = _FakeHttp()
                msg = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "switch_profile", "arguments": {"name": "work"}},
                }
                resp = server.handle_request(
                    msg,
                    http=http,
                    daemon=None,
                    preflight_state={"done": True},
                    fail_open_on_list=False,
                    profile_store=store,
                    profile_sync_manager=_FakeSyncMgr(),
                )
                # Should not have called /mcp/call
                self.assertFalse(any(p == "/mcp/call" for _, p in calls))
                self.assertFalse(resp["result"].get("isError", False))


if __name__ == "__main__":
    unittest.main()
