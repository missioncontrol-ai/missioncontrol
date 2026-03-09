import unittest
from types import SimpleNamespace

from sqlmodel import SQLModel

from app.db import engine
from app.routers.klusters import create_cluster
from app.routers.missions import create_mission
from app.routers.docs import create_doc
import app.routers.docs as docs_router
from app.schemas import KlusterCreate, MissionCreate, DocCreate
from app.services.schema_pack import DEFAULT_SCHEMA_PACK


def _request(email: str):
    return SimpleNamespace(
        state=SimpleNamespace(principal={"email": email, "subject": email}),
        url=SimpleNamespace(path="/tests"),
        app=SimpleNamespace(state=SimpleNamespace(schema_pack=DEFAULT_SCHEMA_PACK)),
        headers={},
    )


class DocsResilienceTests(unittest.TestCase):
    def setUp(self):
        SQLModel.metadata.drop_all(engine)
        SQLModel.metadata.create_all(engine)
        self.req = _request("owner@example.com")

    def test_create_doc_survives_index_failure(self):
        mission = create_mission(MissionCreate(name="m1", owners="owner@example.com"), self.req)
        kluster = create_cluster(KlusterCreate(mission_id=mission.id, name="k1", owners="owner@example.com"), self.req)
        docs_router.index_doc = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("index down"))

        doc = create_doc(DocCreate(kluster_id=kluster.id, title="d1", body="body"), self.req)
        self.assertIsNotNone(doc.id)
        self.assertEqual(doc.title, "d1")


if __name__ == "__main__":
    unittest.main()
