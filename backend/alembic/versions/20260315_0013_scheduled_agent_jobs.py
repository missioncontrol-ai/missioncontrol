"""add scheduled agent jobs table

Revision ID: 20260315_0013
Revises: 20260315_0012
Create Date: 2026-03-15 14:30:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "20260315_0013"
down_revision = "20260315_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    from sqlalchemy import inspect as _inspect

    existing_tables = _inspect(conn).get_table_names()
    if "scheduledagentjob" in existing_tables:
        return

    op.create_table(
        "scheduledagentjob",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("owner_subject", sa.String(), nullable=False, index=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=False, server_default=""),
        sa.Column("cron_expr", sa.String(), nullable=False),
        sa.Column("runtime_kind", sa.String(), nullable=False, server_default="opencode"),
        sa.Column("initial_prompt", sa.Text(), nullable=False, server_default=""),
        sa.Column("system_context", sa.Text(), nullable=True),
        sa.Column("policy_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("last_run_at", sa.DateTime(), nullable=True),
        sa.Column("last_session_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("scheduledagentjob")
