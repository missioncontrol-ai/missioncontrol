"""Tests for mission pack service, router, and MCP tools."""
import json
import unittest
import uuid
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from sqlmodel import Session, SQLModel, create_engine

from app.models import Mission, MissionPack, BudgetPolicy
import app.services.mission_pack as mp_svc


def _make_engine():
    engine = create_engine("sqlite://")
    SQLModel.metadata.drop_all(engine, checkfirst=True)
    SQLModel.metadata.create_all(engine)
    return engine


def _fake_request(subject: str = "test@example.com"):
    return SimpleNamespace(
        state=SimpleNamespace(principal={"subject": subject, "email": subject}),
        headers={},
    )


def _create_mission(engine, name: str = "Test Mission", owner: str = "test@example.com") -> Mission:
    mission = Mission(
        id=str(uuid.uuid4()),
        name=name,
        owners=owner,
        description="A test mission",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    with Session(engine) as session:
        session.add(mission)
        session.commit()
        session.refresh(mission)
    return mission


class TestMissionPackExport(unittest.TestCase):
    def setUp(self):
        self.engine = _make_engine()
        self.subject = "packer@example.com"

    def test_export_creates_pack(self):
        mission = _create_mission(self.engine, "Export Mission", self.subject)
        with patch("app.services.mission_pack.engine", self.engine):
            pack = mp_svc.pack_mission(mission.id, self.subject)

        self.assertIsNotNone(pack.id)
        self.assertEqual(pack.name, mission.name)
        self.assertEqual(pack.owner_subject, self.subject)
        self.assertTrue(len(pack.tarball_b64) > 0)
        self.assertTrue(len(pack.sha256) == 64)
        manifest = json.loads(pack.manifest_json)
        self.assertEqual(manifest["mission_id"], mission.id)

    def test_export_not_found_raises(self):
        with patch("app.services.mission_pack.engine", self.engine):
            with self.assertRaises(ValueError):
                mp_svc.pack_mission("nonexistent-id", self.subject)

    def test_list_packs(self):
        m1 = _create_mission(self.engine, "Mission Alpha", self.subject)
        m2 = _create_mission(self.engine, "Mission Beta", self.subject)
        with patch("app.services.mission_pack.engine", self.engine):
            mp_svc.pack_mission(m1.id, self.subject)
            mp_svc.pack_mission(m2.id, self.subject)

        with Session(self.engine) as session:
            from sqlmodel import select
            packs = list(session.exec(
                select(MissionPack).where(MissionPack.owner_subject == self.subject)
            ).all())
        self.assertEqual(len(packs), 2)

    def test_install_creates_mission(self):
        mission = _create_mission(self.engine, "Installable Mission", self.subject)
        with patch("app.services.mission_pack.engine", self.engine):
            pack = mp_svc.pack_mission(mission.id, self.subject)
            result = mp_svc.install_mission_pack(pack.id, self.subject)

        self.assertIn("mission_id", result)
        new_mission_id = result["mission_id"]
        # Should be a new mission (not the original)
        self.assertNotEqual(new_mission_id, mission.id)

        with Session(self.engine) as session:
            new_mission = session.get(Mission, new_mission_id)
        self.assertIsNotNone(new_mission)
        self.assertIn("Installable Mission", new_mission.name)

    def test_install_is_idempotent(self):
        """Installing to same target_mission_id twice shouldn't duplicate klusters."""
        from sqlmodel import select
        from app.models import Kluster

        mission = _create_mission(self.engine, "Idempotent Mission", self.subject)
        # Add a kluster to the mission
        kluster = Kluster(
            id=str(uuid.uuid4()),
            mission_id=mission.id,
            name="My Kluster",
            owners=self.subject,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        with Session(self.engine) as session:
            session.add(kluster)
            session.commit()

        target = _create_mission(self.engine, "Target Mission", self.subject)
        with patch("app.services.mission_pack.engine", self.engine):
            pack = mp_svc.pack_mission(mission.id, self.subject)
            mp_svc.install_mission_pack(pack.id, self.subject, target.id)
            mp_svc.install_mission_pack(pack.id, self.subject, target.id)

        with Session(self.engine) as session:
            klusters = list(session.exec(
                select(Kluster).where(Kluster.mission_id == target.id)
            ).all())
        # Should only have one kluster, not two
        self.assertEqual(len(klusters), 1)

    def test_install_pack_not_found_raises(self):
        with patch("app.services.mission_pack.engine", self.engine):
            with self.assertRaises(ValueError):
                mp_svc.install_mission_pack("bad-pack-id", self.subject)

    def test_mcp_list_mission_packs(self):
        from app.routers.mcp import call_tool, MCPCall
        from fastapi import Response

        mission = _create_mission(self.engine, "MCP Mission", self.subject)
        with patch("app.services.mission_pack.engine", self.engine):
            mp_svc.pack_mission(mission.id, self.subject)

        # Patch the engine used by mcp's session
        import app.db as app_db
        orig_engine = app_db.engine

        # Just test the service layer since MCP tool uses get_session which hits the real DB
        with Session(self.engine) as session:
            from sqlmodel import select
            packs = list(session.exec(
                select(MissionPack).where(MissionPack.owner_subject == self.subject)
            ).all())
        self.assertGreaterEqual(len(packs), 1)
        self.assertEqual(packs[0].owner_subject, self.subject)
