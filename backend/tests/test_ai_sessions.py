import unittest
from types import SimpleNamespace

from fastapi import HTTPException
from sqlmodel import SQLModel

from app.db import engine
from app.routers import ai as ai_router
from app.services.ai_planner import PlanResult, PlannedToolCall


def _request(subject: str):
    return SimpleNamespace(
        state=SimpleNamespace(principal={"subject": subject, "email": None}),
        headers={},
        url=SimpleNamespace(path="/tests/ai"),
    )


class AiSessionRouterTests(unittest.TestCase):
    def setUp(self):
        SQLModel.metadata.drop_all(engine)
        SQLModel.metadata.create_all(engine)

    def test_create_and_turn_flow(self):
        req = _request("owner@example.com")
        session = ai_router.create_session(ai_router.AiSessionCreate(title="test"), req)
        self.assertTrue(session["id"].startswith("ais_"))

        updated = ai_router.create_turn(session["id"], ai_router.AiTurnCreate(message="help"), req)
        self.assertEqual(updated["id"], session["id"])
        event_types = [e["event_type"] for e in updated["events"]]
        self.assertIn("user_message", event_types)
        self.assertIn("planner_result", event_types)

    def test_write_call_requires_approval_then_executes(self):
        req = _request("owner@example.com")
        session = ai_router.create_session(ai_router.AiSessionCreate(title="test"), req)

        original = ai_router.plan_turn
        ai_router.plan_turn = lambda message: PlanResult(
            assistant_text="plan",
            tool_calls=[PlannedToolCall(tool="create_mission", args={"name": "m1", "owners": "owner@example.com"})],
        )
        try:
            updated = ai_router.create_turn(session["id"], ai_router.AiTurnCreate(message="create mission"), req)
        finally:
            ai_router.plan_turn = original

        pending = [a for a in updated["pending_actions"] if a["status"] == "pending"]
        self.assertEqual(len(pending), 1)

        approved = ai_router.approve_action(session["id"], pending[0]["id"], req)
        self.assertEqual(approved["id"], session["id"])
        event_types = [e["event_type"] for e in approved["events"]]
        self.assertIn("approval_outcome", event_types)
        self.assertIn("tool_result", event_types)

    def test_cross_subject_forbidden(self):
        owner = _request("owner@example.com")
        other = _request("other@example.com")
        session = ai_router.create_session(ai_router.AiSessionCreate(title="test"), owner)

        with self.assertRaises(HTTPException) as ctx:
            ai_router.get_ai_session(session["id"], other)
        self.assertEqual(ctx.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
