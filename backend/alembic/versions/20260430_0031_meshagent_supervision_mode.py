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


def upgrade():
    op.add_column("meshagent", sa.Column("supervision_mode", sa.String, nullable=True))


def downgrade():
    op.drop_column("meshagent", "supervision_mode")
