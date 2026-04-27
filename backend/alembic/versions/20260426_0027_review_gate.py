"""add review_gate table

Revision ID: ccc0426001
Revises: bbb0421001
Create Date: 2026-04-26
"""

from alembic import op
import sqlalchemy as sa
from datetime import datetime

revision = "ccc0426001"
down_revision = "bbb0421001"
branch_labels = None
depends_on = None


def _has_table(conn, name: str) -> bool:
    from sqlalchemy import inspect as _inspect
    return name in _inspect(conn).get_table_names()


def upgrade() -> None:
    conn = op.get_bind()
    if _has_table(conn, "reviewgate"):
        return

    op.create_table(
        "reviewgate",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("owner_subject", sa.String(), nullable=False),
        sa.Column("mesh_task_id", sa.String(), sa.ForeignKey("meshtask.id"), nullable=False),
        sa.Column("run_id", sa.String(), sa.ForeignKey("agentrun.id"), nullable=True),
        sa.Column("gate_type", sa.String(), nullable=False),
        sa.Column("required_approvals", sa.String(), nullable=False, server_default="human"),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("approval_request_id", sa.String(), nullable=True),
        sa.Column("ai_pending_action_id", sa.String(), nullable=True),
        sa.Column("policy_rule_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_reviewgate_owner_subject", "reviewgate", ["owner_subject"])
    op.create_index("ix_reviewgate_mesh_task_id", "reviewgate", ["mesh_task_id"])


def downgrade() -> None:
    op.drop_index("ix_reviewgate_mesh_task_id", "reviewgate")
    op.drop_index("ix_reviewgate_owner_subject", "reviewgate")
    op.drop_table("reviewgate")
