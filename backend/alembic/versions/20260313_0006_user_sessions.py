"""add user_sessions table

Revision ID: 20260313_0006
Revises: 20260309_0005
Create Date: 2026-03-13 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "20260313_0006"
down_revision = "20260309_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    from sqlalchemy import inspect as _inspect
    if "usersession" in set(_inspect(conn).get_table_names()):
        return
    op.create_table(
        "usersession",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("subject", sa.String(), nullable=False, index=True),
        sa.Column("token_hash", sa.String(), nullable=False, unique=True, index=True),
        sa.Column("token_prefix", sa.String(), nullable=False, server_default=""),
        sa.Column("expires_at", sa.DateTime(), nullable=False, index=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=False),
        sa.Column("user_agent", sa.String(), nullable=False, server_default=""),
        sa.Column("revoked", sa.Boolean(), nullable=False, server_default=sa.false(), index=True),
    )


def downgrade() -> None:
    op.drop_table("usersession")
