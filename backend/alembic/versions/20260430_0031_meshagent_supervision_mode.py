"""add supervision_mode to meshagent

Revision ID: ggg0430001
Revises: eee0429001
Create Date: 2026-04-30
"""

from alembic import op
import sqlalchemy as sa

revision = "ggg0430001"
down_revision = "eee0429001"
branch_labels = None
depends_on = None


def _has_column(conn, table: str, column: str) -> bool:
    from sqlalchemy import inspect as _inspect
    return column in [c["name"] for c in _inspect(conn).get_columns(table)]


def upgrade():
    conn = op.get_bind()
    if not _has_column(conn, "meshagent", "supervision_mode"):
        op.add_column("meshagent", sa.Column("supervision_mode", sa.String, nullable=True))


def downgrade():
    op.drop_column("meshagent", "supervision_mode")
