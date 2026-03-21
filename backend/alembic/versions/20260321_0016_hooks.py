"""add claude hooks fields to agent_session

Revision ID: 20260321_0016
Revises: 20260320_0015
Create Date: 2026-03-21 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "20260321_0016"
down_revision = "20260320_0015"
branch_labels = None
depends_on = None


def _has_column(conn, table: str, column: str) -> bool:
    from sqlalchemy import inspect as _inspect
    cols = [c["name"] for c in _inspect(conn).get_columns(table)]
    return column in cols


def upgrade() -> None:
    conn = op.get_bind()

    if not _has_column(conn, "agentsession", "claude_session_id"):
        op.add_column("agentsession", sa.Column("claude_session_id", sa.String(), nullable=True, index=True))

    if not _has_column(conn, "agentsession", "end_reason"):
        op.add_column("agentsession", sa.Column("end_reason", sa.String(), nullable=True))

    if not _has_column(conn, "agentsession", "audit_log"):
        op.add_column("agentsession", sa.Column("audit_log", sa.Text(), nullable=True, server_default=""))


def downgrade() -> None:
    op.drop_column("agentsession", "audit_log")
    op.drop_column("agentsession", "end_reason")
    op.drop_column("agentsession", "claude_session_id")
