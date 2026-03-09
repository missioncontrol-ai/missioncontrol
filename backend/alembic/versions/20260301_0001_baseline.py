"""baseline schema

Revision ID: 20260301_0001
Revises:
Create Date: 2026-03-01 00:00:00.000000

"""

from alembic import op
from sqlmodel import SQLModel

from app import models  # noqa: F401

# revision identifiers, used by Alembic.
revision = "20260301_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    SQLModel.metadata.create_all(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    SQLModel.metadata.drop_all(bind=bind)
