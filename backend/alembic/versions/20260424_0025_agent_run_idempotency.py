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


def _col_set(conn, table: str) -> set:
    from sqlalchemy import inspect as _inspect
    return {c["name"] for c in _inspect(conn).get_columns(table)}


def _constraint_names(conn, table: str) -> set:
    from sqlalchemy import inspect as _inspect
    uqs = _inspect(conn).get_unique_constraints(table)
    return {c["name"] for c in uqs}


def upgrade() -> None:
    conn = op.get_bind()
    cols = _col_set(conn, "agentrun")
    constraints = _constraint_names(conn, "agentrun")

    needs_col = "idempotency_key" not in cols
    needs_constraint = "uq_agentrun_owner_idempotency" not in constraints

    if needs_col or needs_constraint:
        with op.batch_alter_table("agentrun") as batch_op:
            if needs_col:
                batch_op.add_column(sa.Column("idempotency_key", sa.String(), nullable=True))
            if needs_constraint:
                batch_op.create_unique_constraint(
                    "uq_agentrun_owner_idempotency",
                    ["owner_subject", "idempotency_key"],
                )

    import logging
    _log = logging.getLogger(__name__)
    from sqlalchemy import inspect as _inspect
    tables = _inspect(conn).get_table_names()
    _log.warning("DIAG 0025 post-upgrade tables: %s", sorted(tables))


def downgrade() -> None:
    with op.batch_alter_table("agentrun") as batch_op:
        batch_op.drop_constraint("uq_agentrun_owner_idempotency", type_="unique")
        batch_op.drop_column("idempotency_key")
