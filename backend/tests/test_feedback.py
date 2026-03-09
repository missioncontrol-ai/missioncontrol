import unittest
from types import SimpleNamespace

from sqlmodel import SQLModel

from app.db import engine, get_session
from app.models import Mission, MissionRoleMembership
from app.routers.feedback import (
    create_agent_feedback,
    create_human_feedback,
    feedback_summary,
    list_feedback,
    update_feedback_triage,
)
from app.schemas import FeedbackCreate, FeedbackTriageUpdate


def _request(subject: str, email: str):
    return SimpleNamespace(state=SimpleNamespace(principal={"subject": subject, "email": email}))


class FeedbackTests(unittest.TestCase):
    def setUp(self):
        SQLModel.metadata.drop_all(engine)
        SQLModel.metadata.create_all(engine)
        with get_session() as session:
            mission = Mission(id="mission-a", name="mission-a", owners="owner@example.com")
            session.add(mission)
            session.add(
                MissionRoleMembership(
                    mission_id="mission-a",
                    subject="contrib@example.com",
                    role="mission_contributor",
                )
            )
            session.commit()

    def test_create_feedback_and_summary(self):
        req = _request(subject="contrib@example.com", email="contrib@example.com")
        agent = create_agent_feedback(
            FeedbackCreate(
                mission_id="mission-a",
                category="reliability",
                severity="high",
                summary="Agent observed timeout spike",
                recommendation="Increase retry backoff",
            ),
            req,
        )
        human = create_human_feedback(
            FeedbackCreate(
                mission_id="mission-a",
                category="ux",
                severity="low",
                summary="Command help text is unclear",
            ),
            req,
        )
        self.assertEqual(agent["source_type"], "agent")
        self.assertEqual(human["source_type"], "human")

        summary = feedback_summary("mission-a", req)
        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["by_source_type"].get("agent"), 1)
        self.assertEqual(summary["by_source_type"].get("human"), 1)
        self.assertEqual(summary["by_triage_status"].get("new"), 2)
        self.assertEqual(summary["by_priority"].get("p2"), 2)

    def test_triage_update_and_list_filters(self):
        req = _request(subject="contrib@example.com", email="contrib@example.com")
        agent = create_agent_feedback(
            FeedbackCreate(
                mission_id="mission-a",
                category="reliability",
                severity="high",
                summary="Observed retry storm",
            ),
            req,
        )
        update_feedback_triage(
            feedback_id=agent["id"],
            payload=FeedbackTriageUpdate(
                triage_status="accepted",
                priority="p0",
                owner="platform",
                disposition="planned",
                outcome_ref="TASK-123",
            ),
            request=req,
        )
        accepted = list_feedback(mission_id="mission-a", request=req, triage_status="accepted")
        self.assertEqual(len(accepted), 1)
        self.assertEqual(accepted[0]["priority"], "p0")
        self.assertEqual(accepted[0]["owner"], "platform")
        self.assertEqual(accepted[0]["disposition"], "planned")
        self.assertEqual(accepted[0]["outcome_ref"], "TASK-123")


if __name__ == "__main__":
    unittest.main()
