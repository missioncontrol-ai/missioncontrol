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

    if "idempotency_key" not in _col_set(conn, "agentrun"):
        op.add_column("agentrun", sa.Column("idempotency_key", sa.String(), nullable=True))

    if "uq_agentrun_owner_idempotency" not in _constraint_names(conn, "agentrun"):
        op.create_unique_constraint(
            "uq_agentrun_owner_idempotency",
            "agentrun",
            ["owner_subject", "idempotency_key"],
        )

    # DIAGNOSTIC: log which tables exist after this migration
    from sqlalchemy import inspect as _inspect
    import logging
    _log = logging.getLogger(__name__)
    tables = _inspect(conn).get_table_names()
    _log.warning("DIAG 0025 post-upgrade tables: %s", sorted(tables))


def downgrade() -> None:
    op.drop_constraint("uq_agentrun_owner_idempotency", "agentrun", type_="unique")
    op.drop_column("agentrun", "idempotency_key")
