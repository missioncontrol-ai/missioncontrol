"""add oidc web login state/grant tables

Revision ID: 20260314_0009
Revises: 20260314_0008
Create Date: 2026-03-14 20:30:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "20260314_0009"
down_revision = "20260314_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    from sqlalchemy import inspect as _inspect

    tables = set(_inspect(conn).get_table_names())

    if "oidcauthrequest" not in tables:
        op.create_table(
            "oidcauthrequest",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("state", sa.String(), nullable=False, unique=True, index=True),
            sa.Column("code_verifier", sa.Text(), nullable=False, server_default=""),
            sa.Column("nonce", sa.String(), nullable=False, server_default=""),
            sa.Column("redirect_path", sa.Text(), nullable=False, server_default="/ui/"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=False, index=True),
            sa.Column("used_at", sa.DateTime(), nullable=True),
        )

    if "oidclogingrant" not in tables:
        op.create_table(
            "oidclogingrant",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("auth_request_id", sa.String(), nullable=False, index=True),
            sa.Column("subject", sa.String(), nullable=False, index=True),
            sa.Column("email", sa.String(), nullable=False, server_default="", index=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=False, index=True),
            sa.Column("used_at", sa.DateTime(), nullable=True),
        )


def downgrade() -> None:
    op.drop_table("oidclogingrant")
    op.drop_table("oidcauthrequest")
