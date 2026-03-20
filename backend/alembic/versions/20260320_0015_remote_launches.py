"""add remote launch tables and capability_scope to user sessions

Revision ID: 20260320_0015
Revises: 20260315_0014
Create Date: 2026-03-20 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "20260320_0015"
down_revision = "20260315_0014"
branch_labels = None
depends_on = None


def _has_table(conn, name: str) -> bool:
    from sqlalchemy import inspect as _inspect
    return name in _inspect(conn).get_table_names()


def _has_column(conn, table: str, column: str) -> bool:
    from sqlalchemy import inspect as _inspect
    cols = [c["name"] for c in _inspect(conn).get_columns(table)]
    return column in cols


def upgrade() -> None:
    conn = op.get_bind()

    if not _has_table(conn, "remotetarget"):
        op.create_table(
            "remotetarget",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("owner_subject", sa.String(), nullable=False, index=True),
            sa.Column("name", sa.String(), nullable=False, index=True),
            sa.Column("host", sa.String(), nullable=False),
            sa.Column("user", sa.String(), nullable=False, server_default=""),
            sa.Column("port", sa.Integer(), nullable=False, server_default="22"),
            sa.Column("transport", sa.String(), nullable=False, server_default="ssh"),
            sa.Column("ssh_pubkey", sa.Text(), nullable=False, server_default=""),
            sa.Column("key_fingerprint", sa.String(), nullable=False, server_default=""),
            sa.Column("last_used_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("owner_subject", "name", name="uq_remotetarget_owner_name"),
        )

    if not _has_table(conn, "remotelaunchrecord"):
        op.create_table(
            "remotelaunchrecord",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("owner_subject", sa.String(), nullable=False, index=True),
            sa.Column("transport", sa.String(), nullable=False),
            sa.Column("target_id", sa.String(), nullable=True, index=True),
            sa.Column("target_host", sa.String(), nullable=False, server_default=""),
            sa.Column("target_namespace", sa.String(), nullable=False, server_default=""),
            sa.Column("agent_kind", sa.String(), nullable=False),
            sa.Column("agent_profile", sa.String(), nullable=False, server_default=""),
            sa.Column("runtime_session_id", sa.String(), nullable=False, server_default="", index=True),
            sa.Column("session_token_id", sa.Integer(), nullable=True),
            sa.Column("capability_scope", sa.Text(), nullable=False, server_default=""),
            sa.Column("status", sa.String(), nullable=False, server_default="launching", index=True),
            sa.Column("last_heartbeat_at", sa.DateTime(), nullable=True),
            sa.Column("exit_code", sa.Integer(), nullable=True),
            sa.Column("error_message", sa.String(), nullable=False, server_default=""),
            sa.Column("log_tail", sa.Text(), nullable=False, server_default=""),
            sa.Column("mc_binary_path", sa.String(), nullable=False, server_default=""),
            sa.Column("agent_binary_path", sa.String(), nullable=False, server_default=""),
            sa.Column("k8s_job_name", sa.String(), nullable=False, server_default=""),
            sa.Column("mc_version", sa.String(), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=False, index=True),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )

    if _has_table(conn, "usersession") and not _has_column(conn, "usersession", "capability_scope"):
        op.add_column("usersession", sa.Column("capability_scope", sa.Text(), nullable=True, server_default=""))


def downgrade() -> None:
    op.drop_table("remotelaunchrecord")
    op.drop_table("remotetarget")
    op.drop_column("usersession", "capability_scope")
