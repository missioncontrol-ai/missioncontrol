"""add serviceaccount and serviceaccounttoken tables

Revision ID: iii0503001
Revises: hhh0431001
Create Date: 2026-05-03
"""

from alembic import op
import sqlalchemy as sa

revision = "iii0503001"
down_revision = "hhh0431001"
branch_labels = None
depends_on = None


def _has_table(conn, name: str) -> bool:
    from sqlalchemy import inspect as _inspect
    return name in set(_inspect(conn).get_table_names())


def upgrade() -> None:
    conn = op.get_bind()

    if not _has_table(conn, "serviceaccount"):
        op.create_table(
            "serviceaccount",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("owner_subject", sa.String(), nullable=False, index=True),
            sa.Column("client_secret_hash", sa.String(), nullable=False),
            sa.Column("client_secret_prefix", sa.String(), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("revoked", sa.Boolean(), nullable=False, server_default=sa.false(), index=True),
            sa.UniqueConstraint("name", name="uq_serviceaccount_name"),
        )

    if not _has_table(conn, "serviceaccounttoken"):
        op.create_table(
            "serviceaccounttoken",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "service_account_id",
                sa.Integer(),
                sa.ForeignKey("serviceaccount.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column("token_hash", sa.String(), nullable=False, index=True),
            sa.Column("token_prefix", sa.String(), nullable=False, server_default=""),
            sa.Column("expires_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("last_used_at", sa.DateTime(), nullable=True),
            sa.Column("revoked", sa.Boolean(), nullable=False, server_default=sa.false(), index=True),
            sa.UniqueConstraint("token_hash", name="uq_serviceaccounttoken_token_hash"),
        )


def downgrade() -> None:
    op.drop_table("serviceaccounttoken")
    op.drop_table("serviceaccount")
