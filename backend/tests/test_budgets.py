"""Tests for budget service and models."""

import unittest
import uuid
from unittest.mock import patch
from datetime import datetime

from sqlmodel import Session, SQLModel, create_engine

import app.services.budget as budget_svc
from app.models import BudgetPolicy, BudgetWindow, UsageRecord, CostProfile


def _make_test_engine():
    engine = create_engine("sqlite://")
    SQLModel.metadata.drop_all(engine, checkfirst=True)
    SQLModel.metadata.create_all(engine)
    return engine


def _seed_cost_profiles(engine):
    """Seed the 4 default cost profiles into the test DB."""
    now = datetime.utcnow()
    profiles = [
        CostProfile(
            id=str(uuid.uuid4()),
            runtime_kind="claude_code",
            provider="anthropic",
            model="claude-sonnet-4-6",
            input_rate_per_mtok_cents=300,
            output_rate_per_mtok_cents=1500,
            reasoning_rate_per_mtok_cents=0,
            tool_call_flat_cents=0,
            updated_at=now,
        ),
        CostProfile(
            id=str(uuid.uuid4()),
            runtime_kind="codex",
            provider="openai",
            model="o4-mini",
            input_rate_per_mtok_cents=110,
            output_rate_per_mtok_cents=440,
            reasoning_rate_per_mtok_cents=0,
            tool_call_flat_cents=0,
            updated_at=now,
        ),
        CostProfile(
            id=str(uuid.uuid4()),
            runtime_kind="gemini",
            provider="google",
            model="gemini-2.0-flash",
            input_rate_per_mtok_cents=10,
            output_rate_per_mtok_cents=40,
            reasoning_rate_per_mtok_cents=0,
            tool_call_flat_cents=0,
            updated_at=now,
        ),
        CostProfile(
            id=str(uuid.uuid4()),
            runtime_kind="default",
            provider="unknown",
            model="unknown",
            input_rate_per_mtok_cents=0,
            output_rate_per_mtok_cents=0,
            reasoning_rate_per_mtok_cents=0,
            tool_call_flat_cents=0,
            updated_at=now,
        ),
    ]
    with Session(engine) as session:
        for p in profiles:
            session.add(p)
        session.commit()


class BudgetServiceTestCase(unittest.TestCase):
    def setUp(self):
        self.engine = _make_test_engine()
        _seed_cost_profiles(self.engine)
        self._patch = patch.object(budget_svc, "engine", self.engine)
        self._patch.start()
        # Clear cache between tests
        budget_svc._policy_cache.clear()

    def tearDown(self):
        self._patch.stop()
        self.engine.dispose()

    def _make_policy(self, owner="alice@example.com", scope_type="mission",
                     scope_id="m1", window_type="day", hard_cap_cents=1000,
                     soft_cap_cents=None, action_on_breach="alert_only"):
        now = datetime.utcnow()
        policy = BudgetPolicy(
            id=str(uuid.uuid4()),
            owner_subject=owner,
            scope_type=scope_type,
            scope_id=scope_id,
            window_type=window_type,
            hard_cap_cents=hard_cap_cents,
            soft_cap_cents=soft_cap_cents,
            action_on_breach=action_on_breach,
            active=True,
            created_at=now,
            updated_at=now,
        )
        with Session(self.engine) as session:
            session.add(policy)
            session.commit()
            session.refresh(policy)
        return policy

    # ------------------------------------------------------------------

    def test_create_and_list_policy(self):
        policy = self._make_policy()
        self.assertIsNotNone(policy.id)

        with Session(self.engine) as session:
            from sqlmodel import select
            results = list(session.exec(
                select(BudgetPolicy).where(BudgetPolicy.owner_subject == "alice@example.com")
            ).all())
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].scope_id, "m1")

    def test_preflight_no_policies_returns_allow(self):
        # O(1) happy path — no DB call needed
        result = budget_svc.preflight("nobody@example.com", "mission", "any", 500)
        self.assertEqual(result, "allow")

    def test_preflight_under_hard_cap_returns_allow(self):
        self._make_policy(hard_cap_cents=1000)
        budget_svc._policy_cache.clear()
        result = budget_svc.preflight("alice@example.com", "mission", "m1", 500)
        self.assertEqual(result, "allow")

    def test_preflight_at_hard_cap_returns_hard_deny(self):
        self._make_policy(hard_cap_cents=100)
        budget_svc._policy_cache.clear()
        result = budget_svc.preflight("alice@example.com", "mission", "m1", 100)
        self.assertEqual(result, "hard_deny")

    def test_record_usage_creates_record(self):
        record = budget_svc.record_usage(
            owner_subject="alice@example.com",
            runtime_kind="claude_code",
            provider="anthropic",
            model="claude-sonnet-4-6",
            input_tokens=1_000_000,
            output_tokens=0,
        )
        self.assertIsNotNone(record.id)
        self.assertEqual(record.owner_subject, "alice@example.com")
        self.assertEqual(record.runtime_kind, "claude_code")
        # 1M input tokens at 300 cents/MTok = 300 cents
        self.assertEqual(record.cost_cents, 300)

    def test_record_usage_updates_window(self):
        policy = self._make_policy(hard_cap_cents=10_000, scope_type="tenant", scope_id="alice@example.com")
        budget_svc._policy_cache.clear()

        budget_svc.record_usage(
            owner_subject="alice@example.com",
            runtime_kind="claude_code",
            provider="anthropic",
            model="claude-sonnet-4-6",
            input_tokens=1_000_000,  # 300 cents cost
        )

        with Session(self.engine) as session:
            window = session.exec(
                __import__("sqlmodel", fromlist=["select"]).select(BudgetWindow)
                .where(BudgetWindow.policy_id == policy.id)
            ).first()
        self.assertIsNotNone(window)
        self.assertEqual(window.consumed_cents, 300)

    def test_cost_profile_seed_exists(self):
        """Verify the 4 seed cost profiles are present."""
        from sqlmodel import select
        with Session(self.engine) as session:
            profiles = list(session.exec(select(CostProfile)).all())
        self.assertEqual(len(profiles), 4)
        kinds = {p.runtime_kind for p in profiles}
        self.assertIn("claude_code", kinds)
        self.assertIn("codex", kinds)
        self.assertIn("gemini", kinds)
        self.assertIn("default", kinds)

    def test_usage_summary(self):
        budget_svc.record_usage(
            owner_subject="alice@example.com",
            runtime_kind="claude_code",
            provider="anthropic",
            model="claude-sonnet-4-6",
            input_tokens=2_000_000,
            output_tokens=1_000_000,
        )
        summary = budget_svc.get_usage_summary("alice@example.com")
        self.assertEqual(summary["record_count"], 1)
        # 2M input @ 300 = 600, 1M output @ 1500 = 1500 => total 2100
        self.assertEqual(summary["total_cost_cents"], 2100)
        self.assertEqual(summary["total_input_tokens"], 2_000_000)
        self.assertEqual(summary["total_output_tokens"], 1_000_000)


if __name__ == "__main__":
    unittest.main()
