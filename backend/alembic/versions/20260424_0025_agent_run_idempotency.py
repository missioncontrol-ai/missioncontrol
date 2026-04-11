"""add idempotency_key column to agentrun with unique constraint

Revision ID: aaa0424005
Revises: aaa0423004
Create Date: 2026-04-24
"""

from alembic import op
import sqlalchemy as sa

revision = "aaa0424005"
down_revision = "aaa0423004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("agentrun") as batch_op:
        batch_op.add_column(
            sa.Column("idempotency_key", sa.String(), nullable=True)
        )
        batch_op.create_unique_constraint(
            "uq_agentrun_owner_idempotency",
            ["owner_subject", "idempotency_key"],
        )


def downgrade() -> None:
    with op.batch_alter_table("agentrun") as batch_op:
        batch_op.drop_constraint("uq_agentrun_owner_idempotency", type_="unique")
        batch_op.drop_column("idempotency_key")
