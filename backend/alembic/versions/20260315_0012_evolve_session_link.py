"""link evolve run to ai session

Revision ID: 20260315_0012
Revises: 20260315_0011
Create Date: 2026-03-15 14:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "20260315_0012"
down_revision = "20260315_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    from sqlalchemy import inspect as _inspect

    columns = {c["name"] for c in _inspect(conn).get_columns("evolverun")}

    with op.batch_alter_table("evolverun") as batch_op:
        if "ai_session_id" not in columns:
            batch_op.add_column(sa.Column("ai_session_id", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("evolverun") as batch_op:
        batch_op.drop_column("ai_session_id")
