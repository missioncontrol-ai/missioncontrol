"""add evolve mission/run tables

Revision ID: 20260313_0007
Revises: 20260313_0006
Create Date: 2026-03-13 00:30:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "20260313_0007"
down_revision = "20260313_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    from sqlalchemy import inspect as _inspect

    tables = set(_inspect(conn).get_table_names())
    if "evolvemission" not in tables:
        op.create_table(
            "evolvemission",
            sa.Column("mission_id", sa.String(), primary_key=True),
            sa.Column("owner_subject", sa.String(), nullable=False, index=True),
            sa.Column("status", sa.String(), nullable=False, server_default="seeded", index=True),
            sa.Column("spec_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
    if "evolverun" not in tables:
        op.create_table(
            "evolverun",
            sa.Column("run_id", sa.String(), primary_key=True),
            sa.Column("mission_id", sa.String(), nullable=False, index=True),
            sa.Column("owner_subject", sa.String(), nullable=False, index=True),
            sa.Column("agent", sa.String(), nullable=False, server_default="claude", index=True),
            sa.Column("status", sa.String(), nullable=False, server_default="launched", index=True),
            sa.Column("started_at", sa.DateTime(), nullable=False),
        )


def downgrade() -> None:
    op.drop_table("evolverun")
    op.drop_table("evolvemission")
