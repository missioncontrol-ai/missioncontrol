import os
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from sqlmodel import Session, SQLModel, create_engine

from app.models import ApprovalRequest, GovernancePolicy
from app.services.governance import (
    approval_trace_from_context,
    apply_env_overrides,
    DEFAULT_POLICY,
    create_policy_draft,
    ensure_governance_policy_seed,
    evaluate_action,
    extract_approval_context,
    generate_approval_token,
    get_active_policy_row,
    require_policy_action,
    parse_approval_token,
    publish_policy_draft,
)


class GovernanceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        SQLModel.metadata.create_all(self.engine)

    def test_seed_creates_active_policy(self):
        with Session(self.engine) as session:
            row = ensure_governance_policy_seed(session)
            self.assertEqual(row.state, "active")
            self.assertEqual(row.version, 1)

    def test_draft_publish_flow(self):
        with Session(self.engine) as session:
            ensure_governance_policy_seed(session)
            policy = dict(DEFAULT_POLICY)
            policy = {
                **policy,
                "global": {**policy["global"], "allow_update": False},
            }
            draft = create_policy_draft(session=session, actor_subject="admin@example.com", policy=policy)
            self.assertEqual(draft.state, "draft")
            published = publish_policy_draft(
                session=session,
                draft_id=draft.id,
                actor_subject="admin@example.com",
                note="lock updates",
            )
            self.assertEqual(published.state, "active")
            active = get_active_policy_row(session)
            self.assertEqual(active.id, published.id)

    def test_evaluate_action_denies_when_disabled(self):
        policy = {
            **DEFAULT_POLICY,
            "actions": {
                **DEFAULT_POLICY["actions"],
                "task.update": {"enabled": False, "requires_approval": False},
            },
        }
        result = evaluate_action(policy=policy, action="task.update", approval_context=None, channel="api")
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "action_disabled")

    def test_default_policy_allows_doc_and_artifact_updates(self):
        doc_result = evaluate_action(
            policy=DEFAULT_POLICY,
            action="doc.update",
            approval_context=None,
            channel="api",
        )
        artifact_result = evaluate_action(
            policy=DEFAULT_POLICY,
            action="artifact.update",
            approval_context=None,
            channel="api",
        )
        self.assertTrue(doc_result["allowed"])
        self.assertTrue(artifact_result["allowed"])

    def test_parse_approval_token_accepts_valid_signature(self):
        exp = int((datetime.utcnow() + timedelta(minutes=5)).timestamp())
        payload = {
            "request_id": "req-123",
            "approved_by": "owner@example.com",
            "approved_at": datetime.utcnow().isoformat(),
            "nonce": "n-1",
            "exp": exp,
        }
        with patch.dict(os.environ, {"MC_APPROVAL_TOKEN_SECRET": "test-secret"}, clear=False):
            token = generate_approval_token(payload)
            parsed = parse_approval_token(token)
            self.assertIsNotNone(parsed)
            self.assertEqual(parsed["request_id"], "req-123")
            self.assertTrue(parsed.get("token_verified"))

    def test_parse_approval_token_rejects_expired_or_tampered_tokens(self):
        expired_payload = {
            "request_id": "req-123",
            "approved_by": "owner@example.com",
            "approved_at": datetime.utcnow().isoformat(),
            "nonce": "n-2",
            "exp": int((datetime.utcnow() - timedelta(seconds=1)).timestamp()),
        }
        with patch.dict(os.environ, {"MC_APPROVAL_TOKEN_SECRET": "test-secret"}, clear=False):
            expired = generate_approval_token(expired_payload)
            self.assertIsNone(parse_approval_token(expired))

            valid_payload = {
                **expired_payload,
                "exp": int((datetime.utcnow() + timedelta(minutes=5)).timestamp()),
                "nonce": "n-3",
            }
            valid = generate_approval_token(valid_payload)
            parts = valid.split(".")
            self.assertEqual(len(parts), 2)
            sig = parts[1]
            tampered_sig = ("0" if sig[0] != "0" else "1") + sig[1:]
            tampered = f"{parts[0]}.{tampered_sig}"
            self.assertIsNone(parse_approval_token(tampered))

    def test_extract_approval_context_disables_legacy_by_default_when_secret_set(self):
        class _Req:
            headers = {"x-approval-context": '{"request_id":"a","approved_by":"b","approved_at":"c"}'}

        with patch.dict(os.environ, {"MC_APPROVAL_TOKEN_SECRET": "test-secret"}, clear=False):
            self.assertIsNone(extract_approval_context(_Req()))

    def test_production_profile_forces_conservative_mutation_defaults(self):
        with patch.dict(os.environ, {"MC_GOV_PROFILE": "production"}, clear=False):
            policy = apply_env_overrides(DEFAULT_POLICY)
        self.assertTrue(policy["global"]["require_approval_for_mutations"])
        self.assertFalse(policy["global"]["allow_create_without_approval"])
        self.assertFalse(policy["mcp"]["allow_mutation_tools"])
        self.assertFalse(policy["terminal"]["allow_create_actions"])
        self.assertTrue(all(spec.get("requires_approval") for spec in policy["actions"].values()))

    def test_verified_approval_token_is_single_use(self):
        with Session(self.engine) as session:
            ensure_governance_policy_seed(session)
            policy = {
                **DEFAULT_POLICY,
                "global": {
                    **DEFAULT_POLICY["global"],
                    "require_approval_for_mutations": True,
                    "allow_create_without_approval": False,
                },
                "actions": {
                    **DEFAULT_POLICY["actions"],
                    "task.update": {"enabled": True, "requires_approval": True},
                },
            }
            draft = create_policy_draft(session=session, actor_subject="admin@example.com", policy=policy)
            publish_policy_draft(session=session, draft_id=draft.id, actor_subject="admin@example.com")

            approval = ApprovalRequest(
                mission_id="m-1",
                action="task.update",
                status="approved",
                requested_by="contrib@example.com",
                approved_by="owner@example.com",
                approved_at=datetime.utcnow(),
                approval_nonce="nonce-123",
                approval_expires_at=datetime.utcnow() + timedelta(minutes=5),
            )
            session.add(approval)
            session.commit()
            session.refresh(approval)

            token_payload = {
                "approval_request_id": approval.id,
                "mission_id": "m-1",
                "action": "task.update",
                "request_id": "approval-1",
                "approved_by": "owner@example.com",
                "approved_at": datetime.utcnow().isoformat(),
                "nonce": "nonce-123",
                "exp": int((datetime.utcnow() + timedelta(minutes=5)).timestamp()),
            }
            with patch.dict(os.environ, {"MC_APPROVAL_TOKEN_SECRET": "test-secret"}, clear=False):
                token = generate_approval_token(token_payload)
                context = parse_approval_token(token)
                req1 = SimpleNamespace(
                    headers={"x-request-id": "req-1"},
                    state=SimpleNamespace(principal={"email": "contrib@example.com", "subject": "sub-1"}),
                )
                req2 = SimpleNamespace(
                    headers={"x-request-id": "req-2"},
                    state=SimpleNamespace(principal={"email": "contrib@example.com", "subject": "sub-1"}),
                )

                allowed = require_policy_action(
                    session=session,
                    action="task.update",
                    request=req1,
                    approval_context=context,
                    channel="api",
                )
                self.assertTrue(allowed["allowed"])
                with self.assertRaises(HTTPException) as ctx:
                    require_policy_action(
                        session=session,
                        action="task.update",
                        request=req2,
                        approval_context=context,
                        channel="api",
                    )
                self.assertEqual(ctx.exception.detail["reason"], "approval_token_replay")

    def test_approval_trace_extracts_request_id_and_nonce(self):
        trace = approval_trace_from_context(
            {
                "approval_request_id": 42,
                "nonce": "nonce-abc",
            }
        )
        self.assertEqual(trace["approval_request_id"], "42")
        self.assertEqual(trace["approval_nonce"], "nonce-abc")


if __name__ == "__main__":
    unittest.main()
