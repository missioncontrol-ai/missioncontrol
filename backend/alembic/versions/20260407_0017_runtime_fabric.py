"""add runtime fabric node/job/lease tables

Revision ID: 20260407_0017
Revises: 20260321_0016
Create Date: 2026-04-07 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "20260407_0017"
down_revision = "20260321_0016"
branch_labels = None
depends_on = None


def _has_table(conn, name: str) -> bool:
    from sqlalchemy import inspect as _inspect

    return name in _inspect(conn).get_table_names()


def upgrade() -> None:
    conn = op.get_bind()

    if not _has_table(conn, "runtimenode"):
        op.create_table(
            "runtimenode",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("owner_subject", sa.String(), nullable=False, index=True),
            sa.Column("node_name", sa.String(), nullable=False, index=True),
            sa.Column("hostname", sa.String(), nullable=False, server_default=""),
            sa.Column("status", sa.String(), nullable=False, server_default="offline", index=True),
            sa.Column("trust_tier", sa.String(), nullable=False, server_default="untrusted"),
            sa.Column("labels_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("capacity_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("capabilities_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("runtime_version", sa.String(), nullable=False, server_default=""),
            sa.Column("bootstrap_token_prefix", sa.String(), nullable=False, server_default=""),
            sa.Column("last_heartbeat_at", sa.DateTime(), nullable=True),
            sa.Column("registered_at", sa.DateTime(), nullable=False, index=True),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("node_name", name="uq_runtimenode_name"),
        )

    if not _has_table(conn, "runtimejob"):
        op.create_table(
            "runtimejob",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("owner_subject", sa.String(), nullable=False, index=True),
            sa.Column("mission_id", sa.String(), nullable=False, server_default="", index=True),
            sa.Column("task_id", sa.Integer(), nullable=True, index=True),
            sa.Column("runtime_session_id", sa.String(), nullable=False, server_default="", index=True),
            sa.Column("runtime_class", sa.String(), nullable=False, server_default="container", index=True),
            sa.Column("image", sa.String(), nullable=False, server_default=""),
            sa.Column("command", sa.Text(), nullable=False, server_default=""),
            sa.Column("args_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("env_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("cwd", sa.String(), nullable=False, server_default=""),
            sa.Column("mounts_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("artifact_rules_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("timeout_seconds", sa.Integer(), nullable=False, server_default="3600"),
            sa.Column("restart_policy", sa.String(), nullable=False, server_default="never"),
            sa.Column("required_capabilities_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("preferred_labels_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("status", sa.String(), nullable=False, server_default="queued", index=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, index=True),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )

    if not _has_table(conn, "joblease"):
        op.create_table(
            "joblease",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("job_id", sa.String(), nullable=False, index=True),
            sa.Column("node_id", sa.String(), nullable=False, index=True),
            sa.Column("status", sa.String(), nullable=False, server_default="leased", index=True),
            sa.Column("claimed_at", sa.DateTime(), nullable=False, index=True),
            sa.Column("heartbeat_at", sa.DateTime(), nullable=True),
            sa.Column("started_at", sa.DateTime(), nullable=True),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
            sa.Column("exit_code", sa.Integer(), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=False, server_default=""),
            sa.Column("cleanup_status", sa.String(), nullable=False, server_default="pending"),
            sa.Column("created_at", sa.DateTime(), nullable=False, index=True),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )

    if not _has_table(conn, "executionsession"):
        op.create_table(
            "executionsession",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("lease_id", sa.String(), nullable=False, index=True),
            sa.Column("runtime_class", sa.String(), nullable=False, server_default="container", index=True),
            sa.Column("pty_requested", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("attach_token_prefix", sa.String(), nullable=False, server_default=""),
            sa.Column("status", sa.String(), nullable=False, server_default="active", index=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, index=True),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )

    if not _has_table(conn, "nodeevent"):
        op.create_table(
            "nodeevent",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("node_id", sa.String(), nullable=True, index=True),
            sa.Column("lease_id", sa.String(), nullable=True, index=True),
            sa.Column("event_type", sa.String(), nullable=False, index=True),
            sa.Column("payload_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("created_at", sa.DateTime(), nullable=False, index=True),
        )


def downgrade() -> None:
    op.drop_table("nodeevent")
    op.drop_table("executionsession")
    op.drop_table("joblease")
    op.drop_table("runtimejob")
    op.drop_table("runtimenode")
