import asyncio
import unittest
from unittest.mock import patch

from app.services.mesh_events import subscribe, unsubscribe, _publish_local, publish_task_event


class TestMeshEventBus(unittest.IsolatedAsyncioTestCase):
    async def test_subscribe_and_receive(self):
        q = subscribe("kluster:k1")
        try:
            _publish_local("kluster:k1", {"type": "test", "data": 1})
            event = await asyncio.wait_for(q.get(), timeout=1.0)
            self.assertEqual(event["data"], 1)
        finally:
            unsubscribe("kluster:k1", q)

    async def test_unsubscribe_stops_delivery(self):
        q = subscribe("kluster:k2")
        unsubscribe("kluster:k2", q)
        _publish_local("kluster:k2", {"type": "test"})
        self.assertTrue(q.empty())

    async def test_publish_task_event_fans_out_to_kluster_and_mission(self):
        qk = subscribe("kluster:k3")
        qm = subscribe("mission:m3")
        try:
            # Patch _notify_postgres to avoid DB connection in tests
            with patch("app.services.mesh_events._notify_postgres"):
                publish_task_event("task_claimed", "t1", "k3", "m3", status="claimed")
            ek = await asyncio.wait_for(qk.get(), timeout=1.0)
            em = await asyncio.wait_for(qm.get(), timeout=1.0)
            self.assertEqual(ek["kluster_id"], "k3")
            self.assertEqual(em["mission_id"], "m3")
        finally:
            unsubscribe("kluster:k3", qk)
            unsubscribe("mission:m3", qm)

    async def test_full_queue_drops_gracefully(self):
        q = subscribe("kluster:k4")
        q._maxsize = 2
        for i in range(5):
            _publish_local("kluster:k4", {"i": i})
        # Should not raise, just drop after maxsize
        self.assertFalse(q.empty())
        unsubscribe("kluster:k4", q)

    async def test_notify_postgres_skips_on_sqlite(self):
        """_notify_postgres should silently skip on SQLite (no exception)."""
        from app.services.mesh_events import _notify_postgres
        # Should not raise; engine is SQLite in tests
        _notify_postgres({"type": "test", "kluster_id": "k", "mission_id": "m"})
