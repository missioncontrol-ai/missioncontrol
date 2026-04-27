"""add runtime_node_id FK to meshagent

Revision ID: fff0430001
Revises: ddd0428001
Create Date: 2026-04-28
"""

from alembic import op
import sqlalchemy as sa

revision = "fff0430001"
down_revision = "ddd0428001"
branch_labels = None
depends_on = None


def _col_set(conn, table: str) -> set:
    from sqlalchemy import inspect as _inspect
    return {c["name"] for c in _inspect(conn).get_columns(table)}


def upgrade() -> None:
    conn = op.get_bind()

    if "runtime_node_id" not in _col_set(conn, "meshagent"):
        op.add_column("meshagent", sa.Column("runtime_node_id", sa.String(), nullable=True))
        op.create_foreign_key(
            "fk_meshagent_runtime_node_id",
            "meshagent",
            "runtimenode",
            ["runtime_node_id"],
            ["id"],
        )
        op.create_index("ix_meshagent_runtime_node_id", "meshagent", ["runtime_node_id"])


def downgrade() -> None:
    op.drop_index("ix_meshagent_runtime_node_id", table_name="meshagent")
    op.drop_constraint("fk_meshagent_runtime_node_id", "meshagent", type_="foreignkey")
    op.drop_column("meshagent", "runtime_node_id")
