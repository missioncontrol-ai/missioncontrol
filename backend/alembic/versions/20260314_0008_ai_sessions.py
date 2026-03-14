"""add ai session persistence tables

Revision ID: 20260314_0008
Revises: 20260313_0007
Create Date: 2026-03-14 09:30:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "20260314_0008"
down_revision = "20260313_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    from sqlalchemy import inspect as _inspect

    tables = set(_inspect(conn).get_table_names())

    if "aisession" not in tables:
        op.create_table(
            "aisession",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("owner_subject", sa.String(), nullable=False, index=True),
            sa.Column("title", sa.String(), nullable=False, server_default=""),
            sa.Column("status", sa.String(), nullable=False, server_default="active", index=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )

    if "aiturn" not in tables:
        op.create_table(
            "aiturn",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("session_id", sa.String(), nullable=False, index=True),
            sa.Column("role", sa.String(), nullable=False, index=True),
            sa.Column("content_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )

    if "aievent" not in tables:
        op.create_table(
            "aievent",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("session_id", sa.String(), nullable=False, index=True),
            sa.Column("turn_id", sa.Integer(), nullable=True, index=True),
            sa.Column("event_type", sa.String(), nullable=False, index=True),
            sa.Column("payload_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )

    if "aipendingaction" not in tables:
        op.create_table(
            "aipendingaction",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("session_id", sa.String(), nullable=False, index=True),
            sa.Column("turn_id", sa.Integer(), nullable=False, index=True),
            sa.Column("tool", sa.String(), nullable=False, index=True),
            sa.Column("args_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("reason", sa.Text(), nullable=False, server_default=""),
            sa.Column("status", sa.String(), nullable=False, server_default="pending", index=True),
            sa.Column("requested_by", sa.String(), nullable=False, server_default=""),
            sa.Column("approved_by", sa.String(), nullable=False, server_default=""),
            sa.Column("rejected_by", sa.String(), nullable=False, server_default=""),
            sa.Column("rejection_note", sa.Text(), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )


def downgrade() -> None:
    op.drop_table("aipendingaction")
    op.drop_table("aievent")
    op.drop_table("aiturn")
    op.drop_table("aisession")
