import asyncio
import unittest
from types import SimpleNamespace

from fastapi import HTTPException
from sqlmodel import SQLModel

from app.db import engine
from app.routers import evolve as evolve_router


def _request(subject: str):
    return SimpleNamespace(
        state=SimpleNamespace(principal={"subject": subject, "email": None}),
        headers={},
        url=SimpleNamespace(path="/tests/evolve"),
    )


class EvolveRouterTests(unittest.TestCase):
    def setUp(self):
        engine.dispose()
        SQLModel.metadata.drop_all(engine)
        SQLModel.metadata.create_all(engine)

    def test_seed_run_status_happy_path(self):
        owner = _request("owner@example.com")

        seeded = asyncio.run(
            evolve_router.seed_evolve_mission(
                evolve_router.EvolveSpec(spec={"name": "test", "tasks": [{"id": "t1"}]}),
                owner,
            )
        )
        self.assertEqual(seeded["status"], "seeded")
        self.assertEqual(seeded["task_count"], 1)
        mission_id = seeded["mission_id"]

        run = asyncio.run(
            evolve_router.run_evolve_mission(
                mission_id,
                evolve_router.EvolveRunRequest(runtime_kind="claude_code"),
                owner,
            )
        )
        self.assertEqual(run["mission_id"], mission_id)
        self.assertEqual(run["agent"], "claude_code")
        self.assertEqual(run["status"], "running")
        self.assertIn("ai_session_id", run)

        status = asyncio.run(evolve_router.get_evolve_status(mission_id, owner))
        self.assertEqual(status["mission_id"], mission_id)
        self.assertEqual(status["run_count"], 1)
        self.assertEqual(status["task_count"], 1)
        self.assertEqual(status["runs"][0]["agent"], "claude_code")
        self.assertIn("ai_session_id", status["runs"][0])

    def test_cross_subject_access_returns_not_found(self):
        owner = _request("owner@example.com")
        other = _request("other@example.com")
        seeded = asyncio.run(
            evolve_router.seed_evolve_mission(
                evolve_router.EvolveSpec(spec={"name": "test", "tasks": []}),
                owner,
            )
        )
        mission_id = seeded["mission_id"]

        with self.assertRaises(HTTPException) as run_ctx:
            asyncio.run(
                evolve_router.run_evolve_mission(
                    mission_id,
                    evolve_router.EvolveRunRequest(),
                    other,
                )
            )
        self.assertEqual(run_ctx.exception.status_code, 404)

        with self.assertRaises(HTTPException) as status_ctx:
            asyncio.run(evolve_router.get_evolve_status(mission_id, other))
        self.assertEqual(status_ctx.exception.status_code, 404)

    def test_unknown_mission_returns_not_found(self):
        owner = _request("owner@example.com")
        with self.assertRaises(HTTPException) as run_ctx:
            asyncio.run(
                evolve_router.run_evolve_mission(
                    "evolve-missing",
                    evolve_router.EvolveRunRequest(),
                    owner,
                )
            )
        self.assertEqual(run_ctx.exception.status_code, 404)

        with self.assertRaises(HTTPException) as status_ctx:
            asyncio.run(evolve_router.get_evolve_status("evolve-missing", owner))
        self.assertEqual(status_ctx.exception.status_code, 404)

    def test_agent_alias_and_invalid_runtime(self):
        owner = _request("owner@example.com")
        seeded = asyncio.run(
            evolve_router.seed_evolve_mission(
                evolve_router.EvolveSpec(spec={"name": "test", "tasks": []}),
                owner,
            )
        )
        mission_id = seeded["mission_id"]

        run = asyncio.run(
            evolve_router.run_evolve_mission(
                mission_id,
                evolve_router.EvolveRunRequest(agent="claude"),
                owner,
            )
        )
        self.assertEqual(run["runtime_kind"], "claude_code")

        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(
                evolve_router.run_evolve_mission(
                    mission_id,
                    evolve_router.EvolveRunRequest(runtime_kind="not-a-runtime"),
                    owner,
                )
            )
        self.assertEqual(ctx.exception.status_code, 422)


if __name__ == "__main__":
    unittest.main()
