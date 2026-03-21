"""Tests for /remotectl/targets and /remotectl/launches endpoints."""
import unittest
from types import SimpleNamespace
from sqlmodel import SQLModel

from app.db import engine
from app.routers.remotectl import (
    create_target,
    list_targets,
    get_target,
    delete_target,
    create_launch,
    list_launches,
    get_launch,
    complete_launch,
    delete_launch,
    TargetCreate,
    LaunchCreate,
    CompleteUpdate,
)

SUBJECT = "test@example.com"


def _req(subject=SUBJECT):
    return SimpleNamespace(
        state=SimpleNamespace(principal={"subject": subject, "email": subject})
    )


class TestRemoteTargets(unittest.TestCase):
    def setUp(self):
        SQLModel.metadata.drop_all(engine, checkfirst=True)
        SQLModel.metadata.create_all(engine)

    def test_create_and_list_target(self):
        body = TargetCreate(
            name="dev-box",
            host="dev.example.com",
            user="ubuntu",
            transport="ssh",
            ssh_pubkey="ssh-ed25519 AAAA...",
            key_fingerprint="sha256:abc",
        )
        result = create_target(body=body, request=_req())
        self.assertEqual(result.name, "dev-box")

        listed = list_targets(request=_req())
        self.assertEqual(len(listed["targets"]), 1)

    def test_delete_target(self):
        body = TargetCreate(name="box", host="h", user="u", transport="ssh",
                            ssh_pubkey="key", key_fingerprint="fp")
        created = create_target(body=body, request=_req())

        delete_target(target_id=created.id, request=_req())

        listed = list_targets(request=_req())
        self.assertEqual(len(listed["targets"]), 0)

    def test_cross_user_isolation(self):
        body = TargetCreate(name="box", host="h", user="u", transport="ssh",
                            ssh_pubkey="key", key_fingerprint="fp")
        create_target(body=body, request=_req("alice@example.com"))

        listed = list_targets(request=_req("bob@example.com"))
        self.assertEqual(len(listed["targets"]), 0)


class TestRemoteLaunches(unittest.TestCase):
    def setUp(self):
        SQLModel.metadata.drop_all(engine, checkfirst=True)
        SQLModel.metadata.create_all(engine)

    def _make_target(self):
        body = TargetCreate(
            name="box",
            host="remote.example.com",
            user="ubuntu",
            transport="ssh",
            ssh_pubkey="key",
            key_fingerprint="fp",
        )
        return create_target(body=body, request=_req())

    def test_create_launch_returns_token(self):
        target = self._make_target()
        body = LaunchCreate(
            transport="ssh",
            target_id=target.id,
            agent_kind="claude",
            capability_scope=["tools:read", "tools:write"],
        )
        result = create_launch(body=body, request=_req())
        self.assertIn("session_token", result)
        self.assertTrue(result["session_token"].startswith("mcs_"))
        self.assertEqual(result["status"], "launching")

    def test_complete_launch(self):
        target = self._make_target()
        launch = create_launch(
            body=LaunchCreate(transport="ssh", target_id=target.id, agent_kind="claude"),
            request=_req(),
        )
        complete_launch(
            launch_id=launch["id"],
            body=CompleteUpdate(exit_code=0),
            request=_req(),
        )
        status = get_launch(launch_id=launch["id"], request=_req())
        self.assertEqual(status["status"], "completed")

    def test_kill_launch(self):
        target = self._make_target()
        launch = create_launch(
            body=LaunchCreate(transport="ssh", target_id=target.id, agent_kind="claude"),
            request=_req(),
        )
        delete_launch(launch_id=launch["id"], request=_req())
        status = get_launch(launch_id=launch["id"], request=_req())
        self.assertEqual(status["status"], "failed")
