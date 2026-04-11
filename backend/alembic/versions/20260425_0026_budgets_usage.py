"""add budget_policy, budget_window, usage_record, cost_profile tables

Revision ID: bbb0421001
Revises: aaa0424005
Create Date: 2026-04-25
"""

from alembic import op
import sqlalchemy as sa
from datetime import datetime
import uuid

revision = "bbb0421001"
down_revision = "aaa0424005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "budgetpolicy",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("owner_subject", sa.String(), nullable=False, index=True),
        sa.Column("scope_type", sa.String(), nullable=False),
        sa.Column("scope_id", sa.String(), nullable=False),
        sa.Column("window_type", sa.String(), nullable=False),
        sa.Column("hard_cap_cents", sa.Integer(), nullable=False),
        sa.Column("soft_cap_cents", sa.Integer(), nullable=True),
        sa.Column("action_on_breach", sa.String(), nullable=False, server_default="alert_only"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "budgetwindow",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "policy_id",
            sa.String(),
            sa.ForeignKey("budgetpolicy.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("window_start", sa.DateTime(), nullable=False),
        sa.Column("window_end", sa.DateTime(), nullable=False),
        sa.Column("consumed_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("state", sa.String(), nullable=False, server_default="open"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "usagerecord",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("owner_subject", sa.String(), nullable=False, index=True),
        sa.Column(
            "run_id",
            sa.String(),
            sa.ForeignKey("agentrun.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "mesh_task_id",
            sa.String(),
            sa.ForeignKey("meshtask.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "mesh_agent_id",
            sa.String(),
            sa.ForeignKey("meshagent.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("mission_id", sa.String(), nullable=True),
        sa.Column("kluster_id", sa.String(), nullable=True),
        sa.Column("runtime_kind", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False, server_default="unknown"),
        sa.Column("model", sa.String(), nullable=False, server_default="unknown"),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reasoning_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tool_calls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("wall_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("recorded_at", sa.DateTime(), nullable=False),
        sa.Column("source", sa.String(), nullable=False, server_default="adapter"),
    )

    op.create_table(
        "costprofile",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("runtime_kind", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("input_rate_per_mtok_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_rate_per_mtok_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reasoning_rate_per_mtok_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tool_call_flat_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("runtime_kind", "provider", "model", name="uq_costprofile_runtime_provider_model"),
    )

    # Seed default cost profiles
    now = datetime.utcnow()
    op.bulk_insert(
        sa.table(
            "costprofile",
            sa.column("id", sa.String),
            sa.column("runtime_kind", sa.String),
            sa.column("provider", sa.String),
            sa.column("model", sa.String),
            sa.column("input_rate_per_mtok_cents", sa.Integer),
            sa.column("output_rate_per_mtok_cents", sa.Integer),
            sa.column("reasoning_rate_per_mtok_cents", sa.Integer),
            sa.column("tool_call_flat_cents", sa.Integer),
            sa.column("updated_at", sa.DateTime),
        ),
        [
            {
                "id": str(uuid.uuid4()),
                "runtime_kind": "claude_code",
                "provider": "anthropic",
                "model": "claude-sonnet-4-6",
                "input_rate_per_mtok_cents": 300,
                "output_rate_per_mtok_cents": 1500,
                "reasoning_rate_per_mtok_cents": 0,
                "tool_call_flat_cents": 0,
                "updated_at": now,
            },
            {
                "id": str(uuid.uuid4()),
                "runtime_kind": "codex",
                "provider": "openai",
                "model": "o4-mini",
                "input_rate_per_mtok_cents": 110,
                "output_rate_per_mtok_cents": 440,
                "reasoning_rate_per_mtok_cents": 0,
                "tool_call_flat_cents": 0,
                "updated_at": now,
            },
            {
                "id": str(uuid.uuid4()),
                "runtime_kind": "gemini",
                "provider": "google",
                "model": "gemini-2.0-flash",
                "input_rate_per_mtok_cents": 10,
                "output_rate_per_mtok_cents": 40,
                "reasoning_rate_per_mtok_cents": 0,
                "tool_call_flat_cents": 0,
                "updated_at": now,
            },
            {
                "id": str(uuid.uuid4()),
                "runtime_kind": "default",
                "provider": "unknown",
                "model": "unknown",
                "input_rate_per_mtok_cents": 0,
                "output_rate_per_mtok_cents": 0,
                "reasoning_rate_per_mtok_cents": 0,
                "tool_call_flat_cents": 0,
                "updated_at": now,
            },
        ],
    )


def downgrade() -> None:
    op.drop_table("usagerecord")
    op.drop_table("budgetwindow")
    op.drop_table("budgetpolicy")
    op.drop_table("costprofile")
