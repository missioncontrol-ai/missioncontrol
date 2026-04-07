import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

from sqlmodel import SQLModel, Session, create_engine, select

import app.routers.runtime as runtime
from app.models import RuntimeJoinToken, RuntimeNode, RuntimeNodeSpec


SUBJECT = "agent@example.com"


def _request(subject: str = SUBJECT):
    return SimpleNamespace(state=SimpleNamespace(principal={"subject": subject, "email": subject}))


def _bundle_request(subject: str = SUBJECT):
    req = _request(subject)
    req.base_url = "http://testserver/"
    return req


class RuntimeFabricTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        SQLModel.metadata.create_all(self.engine)

    def _session_scope(self):
        @contextmanager
        def _ctx():
            with Session(self.engine) as session:
                yield session

        return _ctx

    def test_join_token_registers_once_and_renders_config(self):
        with patch.object(runtime, "get_session", self._session_scope()), patch.object(
            runtime, "actor_subject_from_request", lambda request: SUBJECT
        ):
            created = runtime.create_join_token(
                runtime.JoinTokenCreate(
                    expires_in_seconds=300,
                    upgrade_channel="stable",
                    desired_version="mc-1.2.3",
                    config={"workspace_root": "/var/lib/mc"},
                ),
                _bundle_request(),
            )
            token = created["join_token"]
            self.assertEqual(created["status"], "active")

            node = runtime.register_node(
                runtime.NodeRegister(
                    node_name="node-a",
                    hostname="node-a.local",
                    trust_tier="trusted",
                    labels={"role": "worker"},
                    capabilities=["container"],
                    runtime_version="",
                    bootstrap_token=token,
                ),
                _request(),
            )

            self.assertEqual(node["runtime_version"], "mc-1.2.3")

            with Session(self.engine) as session:
                stored = session.exec(select(RuntimeJoinToken)).first()
                self.assertIsNotNone(stored)
                self.assertEqual(stored.status, "used")
                self.assertIsNotNone(stored.used_at)

                spec = session.exec(select(RuntimeNodeSpec)).first()
                self.assertIsNotNone(spec)
                self.assertEqual(spec.desired_version, "mc-1.2.3")
                self.assertEqual(spec.upgrade_channel, "stable")

            with self.assertRaises(runtime.HTTPException) as ctx:
                runtime.register_node(
                    runtime.NodeRegister(
                        node_name="node-b",
                        hostname="node-b.local",
                        trust_tier="trusted",
                        bootstrap_token=token,
                    ),
                    _request(),
                )
            self.assertEqual(ctx.exception.status_code, 401)

            payload = runtime.get_node_config(node["id"], _request())
            self.assertIn("config", payload)
            self.assertEqual(payload["config"]["workspace_root"], "/var/lib/mc")

            bundle = runtime.get_node_install_bundle(node["id"], _bundle_request())
            self.assertIn("curl -fsSL", bundle["install_script"])
            self.assertIn("mc node run", bundle["install_script"])
            self.assertEqual(bundle["env"]["MC_BASE_URL"], "http://testserver")
            self.assertEqual(
                bundle["env"]["MC_NODE_BINARY_URL"],
                "http://testserver/runtime/releases/latest/download",
            )
            self.assertEqual(bundle["service"]["name"], "mc-node.service")

            release = runtime.get_runtime_release_manifest()
            self.assertEqual(release["version"], "0.2.0")
            self.assertEqual(release["files"][0]["os"], "linux")
            self.assertEqual(release["files"][0]["arch"], "x86_64")
            self.assertEqual(
                release["files"][0]["url"],
                "https://github.com/missioncontrol-ai/missioncontrol/releases/latest/download/mc-linux-x86_64",
            )

            with Session(self.engine) as session:
                spec = session.exec(select(RuntimeNodeSpec)).first()
                self.assertEqual(spec.config_json, '{"node_name":"node-a","trust_tier":"trusted","labels":{"role":"worker"},"workspace_root":"/var/lib/mc"}')

    def test_cordon_and_drain_update_spec_state(self):
        with patch.object(runtime, "get_session", self._session_scope()), patch.object(
            runtime, "actor_subject_from_request", lambda request: SUBJECT
        ):
            created = runtime.create_join_token(runtime.JoinTokenCreate(), _request())
            node = runtime.register_node(
                runtime.NodeRegister(node_name="node-c", bootstrap_token=created["join_token"]),
                _request(),
            )

            cordoned = runtime.cordon_node(node["id"], _request())
            self.assertEqual(cordoned["spec"]["drain_state"], "cordoned")

            drained = runtime.drain_node(node["id"], _request())
            self.assertEqual(drained["spec"]["drain_state"], "draining")

            upgraded = runtime.upgrade_node(node["id"], _request())
            self.assertEqual(upgraded["spec"]["drain_state"], "upgrading")


if __name__ == "__main__":
    unittest.main()
