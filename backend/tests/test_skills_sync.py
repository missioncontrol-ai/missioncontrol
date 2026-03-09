import base64
import hashlib
import io
import os
import tarfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from sqlmodel import SQLModel

from app.db import engine, get_session
from app.models import Kluster, Mission
from app.services.skills import (
    compute_bundle_signature,
    create_skill_bundle,
    extract_tar_entries,
    get_sync_state,
    resolve_effective_snapshot,
    upsert_sync_state,
)


def _request(email: str):
    return SimpleNamespace(state=SimpleNamespace(principal={"email": email, "subject": "oidc-subject"}))


def _bundle_b64(files: dict[str, str]) -> str:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for path, text in files.items():
            data = text.encode("utf-8")
            info = tarfile.TarInfo(name=path)
            info.size = len(data)
            info.mode = 0o644
            info.mtime = 0
            tf.addfile(info, io.BytesIO(data))
    return base64.b64encode(buf.getvalue()).decode("ascii")


class SkillsSyncTests(unittest.TestCase):
    def setUp(self):
        SQLModel.metadata.drop_all(engine)
        SQLModel.metadata.create_all(engine)
        with get_session() as session:
            session.add(Mission(id="mission-a", name="mission-a", owners="owner@example.com"))
            session.add(Kluster(id="kluster-a", mission_id="mission-a", name="kluster-a"))
            session.commit()

    def test_resolve_snapshot_merges_mission_and_kluster(self):
        req = _request("owner@example.com")
        with get_session() as session:
            create_skill_bundle(
                session=session,
                request=req,
                scope_type="mission",
                scope_id="mission-a",
                mission_id="mission-a",
                kluster_id="",
                manifest_payload={},
                tarball_b64=_bundle_b64({"SKILL.md": "mission", "base.txt": "base"}),
                status="active",
            )
            create_skill_bundle(
                session=session,
                request=req,
                scope_type="kluster",
                scope_id="kluster-a",
                mission_id="mission-a",
                kluster_id="kluster-a",
                manifest_payload={"remove_paths": ["SKILL.md"]},
                tarball_b64=_bundle_b64({"base.txt": "override", "extra.md": "new"}),
                status="active",
            )
            snapshot = resolve_effective_snapshot(session=session, mission_id="mission-a", kluster_id="kluster-a")
            entries = extract_tar_entries(base64.b64decode(snapshot.tarball_b64))

        self.assertNotIn("SKILL.md", entries)
        self.assertEqual(entries["base.txt"].decode("utf-8"), "override")
        self.assertEqual(entries["extra.md"].decode("utf-8"), "new")

    def test_upsert_sync_state_roundtrip(self):
        with get_session() as session:
            upsert_sync_state(
                session=session,
                actor_subject="owner@example.com",
                mission_id="mission-a",
                kluster_id="kluster-a",
                agent_id="agent-1",
                snapshot_id="snap-1",
                snapshot_sha256="sha-1",
                local_overlay_sha256="overlay-1",
                degraded_offline=True,
                drift_flag=True,
                drift_details={"conflicts": ["SKILL.md"]},
            )
            state = get_sync_state(
                session=session,
                actor_subject="owner@example.com",
                mission_id="mission-a",
                kluster_id="kluster-a",
                agent_id="agent-1",
            )

        self.assertIsNotNone(state)
        assert state is not None
        self.assertEqual(state.last_snapshot_id, "snap-1")
        self.assertTrue(state.degraded_offline)
        self.assertTrue(state.drift_flag)

    def test_bundle_signature_required_and_verified_when_secret_set(self):
        req = _request("owner@example.com")
        tarball_b64 = _bundle_b64({"SKILL.md": "mission"})
        tarball_sha = hashlib.sha256(base64.b64decode(tarball_b64)).hexdigest()
        manifest = {
            "files": [{"path": "SKILL.md"}],
        }
        with patch.dict(os.environ, {"MC_SKILLS_SIGNING_SECRET": "secret-1"}, clear=False):
            manifest_preview = {
                "format": "mc-skill-bundle/v1",
                "scope_type": "mission",
                "scope_id": "mission-a",
                "mission_id": "mission-a",
                "kluster_id": "",
                "files": [{"path": "SKILL.md", "sha256": hashlib.sha256(b"mission").hexdigest(), "size": 7}],
                "remove_paths": [],
            }
            signature = compute_bundle_signature(
                manifest=manifest_preview,
                tarball_sha256=tarball_sha,
                secret="secret-1",
                signature_alg="hmac-sha256",
            )
            with get_session() as session:
                bundle = create_skill_bundle(
                    session=session,
                    request=req,
                    scope_type="mission",
                    scope_id="mission-a",
                    mission_id="mission-a",
                    kluster_id="",
                    manifest_payload=manifest,
                    tarball_b64=tarball_b64,
                    status="active",
                    signature_alg="hmac-sha256",
                    signing_key_id="v1",
                    signature=signature,
                )
            self.assertTrue(bundle.signature_verified)

    def test_bundle_signature_missing_rejected_when_secret_set(self):
        req = _request("owner@example.com")
        with patch.dict(os.environ, {"MC_SKILLS_SIGNING_SECRET": "secret-1"}, clear=False):
            with get_session() as session:
                with self.assertRaises(HTTPException) as ctx:
                    create_skill_bundle(
                        session=session,
                        request=req,
                        scope_type="mission",
                        scope_id="mission-a",
                        mission_id="mission-a",
                        kluster_id="",
                        manifest_payload={},
                        tarball_b64=_bundle_b64({"SKILL.md": "mission"}),
                        status="active",
                    )
        self.assertIn("signature is required", str(ctx.exception.detail))


if __name__ == "__main__":
    unittest.main()
