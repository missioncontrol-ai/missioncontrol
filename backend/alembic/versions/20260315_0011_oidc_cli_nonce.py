"""add cli_nonce to oidc auth request and grant tables

Revision ID: 20260315_0011
Revises: 20260315_0010
Create Date: 2026-03-15 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "20260315_0011"
down_revision = "20260315_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    from sqlalchemy import inspect as _inspect

    cols = {c["name"] for c in _inspect(conn).get_columns("oidcauthrequest")}
    if "cli_nonce" not in cols:
        op.add_column("oidcauthrequest", sa.Column("cli_nonce", sa.String(), nullable=True))

    cols = {c["name"] for c in _inspect(conn).get_columns("oidclogingrant")}
    if "cli_nonce" not in cols:
        op.add_column("oidclogingrant", sa.Column("cli_nonce", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("oidclogingrant", "cli_nonce")
    op.drop_column("oidcauthrequest", "cli_nonce")
