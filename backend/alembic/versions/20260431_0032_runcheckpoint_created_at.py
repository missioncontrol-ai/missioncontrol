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


def upgrade() -> None:
    op.add_column(
        "runcheckpoint",
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_runcheckpoint_created_at", "runcheckpoint", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_runcheckpoint_created_at", table_name="runcheckpoint")
    op.drop_column("runcheckpoint", "created_at")
