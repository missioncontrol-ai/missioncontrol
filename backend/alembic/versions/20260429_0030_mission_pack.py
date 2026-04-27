"""add missionpack table

Revision ID: eee0429001
Revises: fff0430001
Create Date: 2026-04-29
"""

from alembic import op
import sqlalchemy as sa
from datetime import datetime

revision = "eee0429001"
down_revision = "fff0430001"
branch_labels = None
depends_on = None


def _has_table(conn, name: str) -> bool:
    from sqlalchemy import inspect as _inspect
    return name in _inspect(conn).get_table_names()


def upgrade():
    conn = op.get_bind()
    if _has_table(conn, "missionpack"):
        return

    op.create_table(
        "missionpack",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("owner_subject", sa.String, nullable=False, index=True),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("sha256", sa.String, nullable=False),
        sa.Column("signature", sa.String, nullable=True),
        sa.Column("tarball_b64", sa.Text, nullable=False),
        sa.Column("manifest_json", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
        sa.UniqueConstraint("owner_subject", "name", "version", name="uq_missionpack_owner_name_version"),
    )


def downgrade():
    op.drop_table("missionpack")
