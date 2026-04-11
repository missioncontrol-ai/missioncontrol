"""add mc-mesh work model tables

Revision ID: 20260410_0019
Revises: 20260407_0018
Create Date: 2026-04-10 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "20260410_0019"
down_revision = "20260407_0018"
branch_labels = None
depends_on = None


def _has_table(conn, name: str) -> bool:
    from sqlalchemy import inspect as _inspect

    return name in _inspect(conn).get_table_names()


def upgrade() -> None:
    conn = op.get_bind()

    # meshtask — agent-executable work unit inside a kluster DAG
    if not _has_table(conn, "meshtask"):
        op.create_table(
            "meshtask",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("kluster_id", sa.String(), nullable=False, index=True),
            sa.Column("mission_id", sa.String(), nullable=False, index=True),
            sa.Column("parent_task_id", sa.String(), nullable=True, index=True),
            sa.Column("title", sa.String(), nullable=False),
            sa.Column("description", sa.Text(), nullable=False, server_default=""),
            sa.Column("input_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("claim_policy", sa.String(), nullable=False, server_default="first_claim"),
            sa.Column("depends_on", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("produces", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("consumes", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("required_capabilities", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("status", sa.String(), nullable=False, server_default="pending", index=True),
            sa.Column("claimed_by_agent_id", sa.String(), nullable=True, index=True),
            sa.Column("result_artifact_id", sa.String(), nullable=True),
            sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
            sa.Column("created_by_subject", sa.String(), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )

    # meshagent — agent runtime in a mission's durable pool
    if not _has_table(conn, "meshagent"):
        op.create_table(
            "meshagent",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("mission_id", sa.String(), nullable=False, index=True),
            sa.Column("node_id", sa.String(), nullable=True, index=True),
            sa.Column("runtime_kind", sa.String(), nullable=False, index=True),
            sa.Column("runtime_version", sa.String(), nullable=False, server_default=""),
            sa.Column("capabilities", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("labels", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("status", sa.String(), nullable=False, server_default="offline", index=True),
            sa.Column("current_task_id", sa.String(), nullable=True, index=True),
            sa.Column("enrolled_by_subject", sa.String(), nullable=False, server_default=""),
            sa.Column("enrolled_at", sa.DateTime(), nullable=False),
            sa.Column("last_heartbeat_at", sa.DateTime(), nullable=True),
        )

    # meshprogressevent — typed progress events streamed by agents
    if not _has_table(conn, "meshprogressevent"):
        op.create_table(
            "meshprogressevent",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("task_id", sa.String(), nullable=False, index=True),
            sa.Column("agent_id", sa.String(), nullable=False, index=True),
            sa.Column("seq", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("event_type", sa.String(), nullable=False, index=True),
            sa.Column("phase", sa.String(), nullable=True),
            sa.Column("step", sa.String(), nullable=True),
            sa.Column("summary", sa.Text(), nullable=False, server_default=""),
            sa.Column("payload_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("occurred_at", sa.DateTime(), nullable=False, index=True),
        )

    # meshmessage — mission/kluster-scoped typed inter-agent messages
    if not _has_table(conn, "meshmessage"):
        op.create_table(
            "meshmessage",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("mission_id", sa.String(), nullable=False, index=True),
            sa.Column("kluster_id", sa.String(), nullable=True, index=True),
            sa.Column("from_agent_id", sa.String(), nullable=False, index=True),
            sa.Column("to_agent_id", sa.String(), nullable=True, index=True),
            sa.Column("task_id", sa.String(), nullable=True, index=True),
            sa.Column("channel", sa.String(), nullable=False, server_default="coordination"),
            sa.Column("body_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("in_reply_to", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, index=True),
            sa.Column("read_at", sa.DateTime(), nullable=True),
        )

    # meshtaskartifact — link table: meshtask → artifact ledger
    if not _has_table(conn, "meshtaskartifact"):
        op.create_table(
            "meshtaskartifact",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("task_id", sa.String(), nullable=False, index=True),
            sa.Column("artifact_id", sa.Integer(), nullable=False, index=True),
            sa.Column("artifact_name", sa.String(), nullable=False, server_default=""),
            sa.Column("role", sa.String(), nullable=False, server_default="output"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )


def downgrade() -> None:
    op.drop_table("meshtaskartifact")
    op.drop_table("meshmessage")
    op.drop_table("meshprogressevent")
    op.drop_table("meshagent")
    op.drop_table("meshtask")
