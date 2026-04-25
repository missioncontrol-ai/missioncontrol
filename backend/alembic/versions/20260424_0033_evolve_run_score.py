"""add score and recipe_path to evolverun

Revision ID: iii0424001
Revises: hhh0431001
Create Date: 2026-04-24
"""

from alembic import op
import sqlalchemy as sa

revision = "iii0424001"
down_revision = "hhh0431001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("evolverun", sa.Column("score", sa.Float(), nullable=True))
    op.add_column("evolverun", sa.Column("recipe_path", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("evolverun", "recipe_path")
    op.drop_column("evolverun", "score")
