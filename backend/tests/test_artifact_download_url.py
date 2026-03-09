import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from sqlmodel import SQLModel

from app.db import engine, get_session
from app.models import Artifact, Kluster, Mission, MissionRoleMembership
from app.routers.artifacts import get_artifact_download_url


def _request(email: str, subject: str = "oidc-subject"):
    return SimpleNamespace(state=SimpleNamespace(principal={"email": email, "subject": subject}))


class ArtifactDownloadUrlTests(unittest.TestCase):
    def setUp(self):
        SQLModel.metadata.drop_all(engine)
        SQLModel.metadata.create_all(engine)
        with get_session() as session:
            session.add(Mission(id="mission-a", name="mission-a", owners="owner-a@example.com"))
            session.add(Mission(id="mission-b", name="mission-b", owners="owner-b@example.com"))
            session.add(Kluster(id="kluster-a", mission_id="mission-a", name="kluster-a", owners="owner-a@example.com"))
            session.add(Kluster(id="kluster-b", mission_id="mission-b", name="kluster-b", owners="owner-b@example.com"))
            session.add(MissionRoleMembership(mission_id="mission-a", subject="viewer@example.com", role="mission_viewer"))
            session.add(
                Artifact(
                    id=1,
                    kluster_id="kluster-a",
                    name="artifact-a",
                    uri="s3://missioncontrol-dev/missions/mission-a/klusters/kluster-a/artifacts/a-json",
                    storage_backend="s3",
                    artifact_type="file",
                )
            )
            session.add(
                Artifact(
                    id=2,
                    kluster_id="kluster-b",
                    name="artifact-b",
                    uri="s3://missioncontrol-dev/missions/mission-b/klusters/kluster-b/artifacts/b-json",
                    storage_backend="s3",
                    artifact_type="file",
                )
            )
            session.commit()

        self.viewer_req = _request(email="viewer@example.com")

    def test_download_url_is_allowed_for_readable_mission(self):
        with patch("app.routers.artifacts.presign_get_uri", return_value="https://signed.example/object"):
            payload = get_artifact_download_url(1, self.viewer_req, 120)
        self.assertEqual(payload["artifact_id"], 1)
        self.assertEqual(payload["expires_seconds"], 120)
        self.assertTrue(payload["download_url"].startswith("https://"))

    def test_download_url_forbidden_for_other_mission(self):
        with patch("app.routers.artifacts.presign_get_uri", return_value="https://signed.example/object"):
            with self.assertRaises(HTTPException) as ctx:
                get_artifact_download_url(2, self.viewer_req, 120)
        self.assertEqual(ctx.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
