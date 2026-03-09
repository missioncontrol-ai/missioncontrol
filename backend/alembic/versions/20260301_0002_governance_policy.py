"""governance policy tables

Revision ID: 20260301_0002
Revises: 20260301_0001
Create Date: 2026-03-01 03:30:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision = "20260301_0002"
down_revision = "20260301_0001"
branch_labels = None
depends_on = None


def _drop_index_if_exists(inspector, table_name: str, index_name: str) -> None:
    existing = {idx.get("name") for idx in inspector.get_indexes(table_name)}
    if index_name in existing:
        op.drop_index(index_name, table_name=table_name)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if not inspector.has_table("governancepolicy"):
        op.create_table(
            "governancepolicy",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("state", sa.String(), nullable=False),
            sa.Column("policy_json", sa.Text(), nullable=False),
            sa.Column("change_note", sa.Text(), nullable=False),
            sa.Column("created_by", sa.String(), nullable=False),
            sa.Column("published_by", sa.String(), nullable=False),
            sa.Column("published_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_governancepolicy_version", "governancepolicy", ["version"])
        op.create_index("ix_governancepolicy_state", "governancepolicy", ["state"])
        op.create_index("ix_governancepolicy_created_at", "governancepolicy", ["created_at"])

    if not inspector.has_table("governancepolicyevent"):
        op.create_table(
            "governancepolicyevent",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("policy_id", sa.Integer(), nullable=True),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("event_type", sa.String(), nullable=False),
            sa.Column("actor_subject", sa.String(), nullable=False),
            sa.Column("detail_json", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_governancepolicyevent_policy_id", "governancepolicyevent", ["policy_id"])
        op.create_index("ix_governancepolicyevent_version", "governancepolicyevent", ["version"])
        op.create_index("ix_governancepolicyevent_event_type", "governancepolicyevent", ["event_type"])
        op.create_index("ix_governancepolicyevent_created_at", "governancepolicyevent", ["created_at"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if inspector.has_table("governancepolicyevent"):
        _drop_index_if_exists(inspector, "governancepolicyevent", "ix_governancepolicyevent_created_at")
        _drop_index_if_exists(inspector, "governancepolicyevent", "ix_governancepolicyevent_event_type")
        _drop_index_if_exists(inspector, "governancepolicyevent", "ix_governancepolicyevent_version")
        _drop_index_if_exists(inspector, "governancepolicyevent", "ix_governancepolicyevent_policy_id")
        op.drop_table("governancepolicyevent")

    if inspector.has_table("governancepolicy"):
        _drop_index_if_exists(inspector, "governancepolicy", "ix_governancepolicy_created_at")
        _drop_index_if_exists(inspector, "governancepolicy", "ix_governancepolicy_state")
        _drop_index_if_exists(inspector, "governancepolicy", "ix_governancepolicy_version")
        op.drop_table("governancepolicy")
