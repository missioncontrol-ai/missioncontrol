"""add created_at to runcheckpoint

Revision ID: hhh0431001
Revises: ggg0430001
Create Date: 2026-04-20
"""

from alembic import op
import sqlalchemy as sa

revision = "hhh0431001"
down_revision = "ggg0430001"
branch_labels = None
depends_on = None


def _has_column(conn, table: str, column: str) -> bool:
    from sqlalchemy import inspect as _inspect
    return column in [c["name"] for c in _inspect(conn).get_columns(table)]


def _has_index(conn, table: str, index: str) -> bool:
    from sqlalchemy import inspect as _inspect
    return any(i["name"] == index for i in _inspect(conn).get_indexes(table))


def upgrade() -> None:
    conn = op.get_bind()
    if not _has_column(conn, "runcheckpoint", "created_at"):
        op.add_column(
            "runcheckpoint",
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )
    if not _has_index(conn, "runcheckpoint", "ix_runcheckpoint_created_at"):
        op.create_index("ix_runcheckpoint_created_at", "runcheckpoint", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_runcheckpoint_created_at", table_name="runcheckpoint")
    op.drop_column("runcheckpoint", "created_at")
