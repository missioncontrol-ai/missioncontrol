"""add runtime fields to aisession

Revision ID: 20260315_0010
Revises: 20260314_0009
Create Date: 2026-03-15 10:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "20260315_0010"
down_revision = "20260314_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    from sqlalchemy import inspect as _inspect

    columns = {c["name"] for c in _inspect(conn).get_columns("aisession")}

    with op.batch_alter_table("aisession") as batch_op:
        if "runtime_kind" not in columns:
            batch_op.add_column(sa.Column("runtime_kind", sa.String(), nullable=False, server_default="opencode"))
        if "runtime_session_id" not in columns:
            batch_op.add_column(sa.Column("runtime_session_id", sa.String(), nullable=True))
        if "workspace_path" not in columns:
            batch_op.add_column(sa.Column("workspace_path", sa.String(), nullable=True))
        if "policy_json" not in columns:
            batch_op.add_column(sa.Column("policy_json", sa.Text(), nullable=False, server_default="{}"))
        if "capability_snapshot_json" not in columns:
            batch_op.add_column(sa.Column("capability_snapshot_json", sa.Text(), nullable=False, server_default="{}"))


def downgrade() -> None:
    with op.batch_alter_table("aisession") as batch_op:
        batch_op.drop_column("capability_snapshot_json")
        batch_op.drop_column("policy_json")
        batch_op.drop_column("workspace_path")
        batch_op.drop_column("runtime_session_id")
        batch_op.drop_column("runtime_kind")
