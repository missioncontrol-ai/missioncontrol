"""add event_trigger table and scheduler mesh_task target columns

Revision ID: ddd0428001
Revises: ccc0426001
Create Date: 2026-04-27
"""

from alembic import op
import sqlalchemy as sa
from datetime import datetime

revision = "ddd0428001"
down_revision = "ccc0426001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add columns to scheduledagentjob (if they don't exist)
    with op.batch_alter_table("scheduledagentjob") as batch_op:
        batch_op.add_column(sa.Column("target_type", sa.String(), nullable=True, server_default="ai_session"))
        batch_op.add_column(sa.Column("target_spec_json", sa.Text(), nullable=True))

    op.create_table(
        "eventtrigger",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("owner_subject", sa.String(), nullable=False, index=True),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("predicate_json", sa.Text(), nullable=True),
        sa.Column("target_type", sa.String(), nullable=False, server_default="mesh_task"),
        sa.Column("target_spec_json", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("cooldown_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_fired_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_eventtrigger_owner_subject", "eventtrigger", ["owner_subject"])


def downgrade() -> None:
    op.drop_table("eventtrigger")
    with op.batch_alter_table("scheduledagentjob") as batch_op:
        batch_op.drop_column("target_spec_json")
        batch_op.drop_column("target_type")
