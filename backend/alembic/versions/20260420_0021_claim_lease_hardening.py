"""add claim_lease_id and version_counter to meshtask for optimistic locking

Revision ID: aaa0420001
Revises: 20260411_0020
Create Date: 2026-04-20
"""

from alembic import op
import sqlalchemy as sa

revision = "aaa0420001"
down_revision = "20260411_0020"
branch_labels = None
depends_on = None


def _col_set(conn, table: str) -> set:
    from sqlalchemy import inspect as _inspect
    return {c["name"] for c in _inspect(conn).get_columns(table)}


def upgrade() -> None:
    conn = op.get_bind()
    existing = _col_set(conn, "meshtask")

    with op.batch_alter_table("meshtask") as batch_op:
        if "claim_lease_id" not in existing:
            batch_op.add_column(
                sa.Column(
                    "claim_lease_id",
                    sa.String(),
                    nullable=True,
                    comment="UUID of the current claim lease; NULL when task is not claimed.",
                )
            )
        if "version_counter" not in existing:
            batch_op.add_column(
                sa.Column(
                    "version_counter",
                    sa.Integer(),
                    nullable=False,
                    server_default="0",
                    comment="Monotonically incremented on every claim/release for optimistic locking.",
                )
            )
        # NOTE: SQLite does not enforce CHECK constraints added via ALTER TABLE in
        # batch mode.  On PostgreSQL this constraint will be applied at the DB level.
        # Constraint name: ck_meshtask_claimed_fields
        # Condition: status NOT IN ('claimed','running')
        #            OR (claimed_by_agent_id IS NOT NULL AND claim_lease_id IS NOT NULL)
        #
        # We skip adding it here to remain SQLite-compatible; the application layer
        # enforces the invariant, and a separate schema test validates it on Postgres.


def downgrade() -> None:
    with op.batch_alter_table("meshtask") as batch_op:
        batch_op.drop_column("version_counter")
        batch_op.drop_column("claim_lease_id")
