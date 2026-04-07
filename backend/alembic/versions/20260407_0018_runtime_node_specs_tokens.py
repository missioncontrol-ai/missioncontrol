"""add runtime node spec and join token tables

Revision ID: 20260407_0018
Revises: 20260407_0017
Create Date: 2026-04-07 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "20260407_0018"
down_revision = "20260407_0017"
branch_labels = None
depends_on = None


def _has_table(conn, name: str) -> bool:
    from sqlalchemy import inspect as _inspect

    return name in _inspect(conn).get_table_names()


def upgrade() -> None:
    conn = op.get_bind()

    if not _has_table(conn, "runtimenodespec"):
        op.create_table(
            "runtimenodespec",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("owner_subject", sa.String(), nullable=False, index=True),
            sa.Column("node_id", sa.String(), nullable=False, index=True),
            sa.Column("config_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("desired_version", sa.String(), nullable=False, server_default=""),
            sa.Column("upgrade_channel", sa.String(), nullable=False, server_default="stable"),
            sa.Column("drain_state", sa.String(), nullable=False, server_default="active"),
            sa.Column("health_summary", sa.Text(), nullable=False, server_default=""),
            sa.Column("config_hash", sa.String(), nullable=False, server_default=""),
            sa.Column("last_reconcile_at", sa.DateTime(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )

    if not _has_table(conn, "runtimejointoken"):
        op.create_table(
            "runtimejointoken",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("owner_subject", sa.String(), nullable=False, index=True),
            sa.Column("token_hash", sa.String(), nullable=False),
            sa.Column("config_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("upgrade_channel", sa.String(), nullable=False, server_default="stable"),
            sa.Column("desired_version", sa.String(), nullable=False, server_default=""),
            sa.Column("expires_at", sa.DateTime(), nullable=True),
            sa.Column("used_at", sa.DateTime(), nullable=True),
            sa.Column("status", sa.String(), nullable=False, server_default="active"),
            sa.Column("rotation_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("node_id", sa.String(), nullable=True, index=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )


def downgrade() -> None:
    op.drop_table("runtimejointoken")
    op.drop_table("runtimenodespec")
