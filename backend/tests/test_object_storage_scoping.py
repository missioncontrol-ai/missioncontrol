import unittest
from unittest.mock import patch

from app.services.object_storage import (
    ObjectStorageConfig,
    build_scoped_key,
    get_bytes_from_uri,
    scoped_prefix,
)


class ObjectStorageScopingTests(unittest.TestCase):
    def test_scoped_prefix_and_key(self):
        prefix = scoped_prefix(mission_id="Mission A", kluster_id="Kluster 1")
        key = build_scoped_key(
            mission_id="Mission A",
            kluster_id="Kluster 1",
            entity="artifacts",
            filename="My File.json",
        )
        self.assertEqual(prefix, "missions/mission-a/klusters/kluster-1/")
        self.assertEqual(key, "missions/mission-a/klusters/kluster-1/artifacts/my-file-json")

    def test_get_bytes_rejects_bucket_or_prefix_escape(self):
        cfg = ObjectStorageConfig(
            endpoint="http://localhost:9000",
            region="us-east-1",
            bucket="missioncontrol-dev",
            access_key="x",
            secret_key="y",
            secure=False,
        )
        with patch("app.services.object_storage.load_object_storage_config", return_value=cfg):
            with self.assertRaises(PermissionError):
                get_bytes_from_uri(
                    "s3://other-bucket/missions/m1/klusters/k1/artifacts/a.json",
                    expected_prefix="missions/m1/klusters/k1/",
                )
            with self.assertRaises(PermissionError):
                get_bytes_from_uri(
                    "s3://missioncontrol-dev/missions/other/klusters/k1/artifacts/a.json",
                    expected_prefix="missions/m1/klusters/k1/",
                )


if __name__ == "__main__":
    unittest.main()
