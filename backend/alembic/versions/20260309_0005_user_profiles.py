"""add userprofile table

Revision ID: 20260309_0005
Revises: 20260307_0004
Create Date: 2026-03-09 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260309_0005"
down_revision = "20260307_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "userprofile",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(), nullable=False, index=True),
        sa.Column("owner_subject", sa.String(), nullable=False, index=True),
        sa.Column("description", sa.String(), nullable=False, server_default=""),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("manifest_json", sa.String(), nullable=False, server_default="[]"),
        sa.Column("tarball_b64", sa.Text(), nullable=True),
        sa.Column("sha256", sa.String(), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("owner_subject", "name", name="uq_userprofile_owner_name"),
    )


def downgrade() -> None:
    op.drop_table("userprofile")
