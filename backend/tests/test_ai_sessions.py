"""Tests for the AI Console router — updated for the async gateway architecture."""
import asyncio
import unittest
from types import SimpleNamespace

from fastapi import HTTPException
from sqlmodel import SQLModel

from app.db import engine
from app.routers import ai as ai_router


def _request(subject: str):
    return SimpleNamespace(
        state=SimpleNamespace(principal={"subject": subject, "email": None}),
        headers={},
        url=SimpleNamespace(path="/tests/ai"),
    )


def _run(coro):
    """Run a coroutine in a fresh event loop (test helper)."""
    return asyncio.get_event_loop().run_until_complete(coro)


class AiSessionRouterTests(unittest.TestCase):
    def setUp(self):
        SQLModel.metadata.drop_all(engine, checkfirst=True)
        SQLModel.metadata.create_all(engine)

    def test_create_session_returns_valid_id(self):
        req = _request("owner@example.com")
        session = _run(ai_router.create_session(ai_router.AiSessionCreate(title="test"), req))
        self.assertTrue(session["id"].startswith("ais_"))
        self.assertEqual(session["owner_subject"], "owner@example.com")
        self.assertIn("runtime_kind", session)
        self.assertEqual(session["runtime_kind"], "opencode")

    def test_create_and_turn_flow(self):
        req = _request("owner@example.com")
        session = _run(ai_router.create_session(ai_router.AiSessionCreate(title="test"), req))
        self.assertTrue(session["id"].startswith("ais_"))

        # Turn submission: opencode will be unreachable in tests, expect graceful error event.
        updated = _run(ai_router.create_turn(session["id"], ai_router.AiTurnCreate(message="help"), req))
        self.assertEqual(updated["id"], session["id"])
        event_types = [e["event_type"] for e in updated["events"]]
        self.assertIn("user_message", event_types)
        # When opencode is unavailable the gateway emits runtime_error
        self.assertIn("runtime_error", event_types)

    def test_cross_subject_forbidden(self):
        owner = _request("owner@example.com")
        other = _request("other@example.com")
        session = _run(ai_router.create_session(ai_router.AiSessionCreate(title="test"), owner))

        with self.assertRaises(HTTPException) as ctx:
            ai_router.get_ai_session(session["id"], other)
        self.assertEqual(ctx.exception.status_code, 404)

    def test_runtime_capabilities_endpoint(self):
        caps = ai_router.get_runtime_capabilities()
        self.assertIsInstance(caps, list)
        self.assertGreater(len(caps), 0)
        kinds = [c["runtime_kind"] for c in caps]
        self.assertIn("opencode", kinds)

    def test_approve_action_not_found(self):
        req = _request("owner@example.com")
        session = _run(ai_router.create_session(ai_router.AiSessionCreate(title="test"), req))
        with self.assertRaises(HTTPException) as ctx:
            _run(ai_router.approve_action(session["id"], "nonexistent_action_id", req))
        self.assertEqual(ctx.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
