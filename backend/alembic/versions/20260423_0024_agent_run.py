"""add agent_run, run_checkpoint tables and agent_run_id to meshprogressevent

Revision ID: aaa0423004
Revises: aaa0420001
Create Date: 2026-04-23
"""

from alembic import op
import sqlalchemy as sa

revision = "aaa0423004"
down_revision = "aaa0420001"
branch_labels = None
depends_on = None


def _has_table(conn, name: str) -> bool:
    from sqlalchemy import inspect as _inspect
    return name in _inspect(conn).get_table_names()


def _col_set(conn, table: str) -> set:
    from sqlalchemy import inspect as _inspect
    return {c["name"] for c in _inspect(conn).get_columns(table)}


def upgrade() -> None:
    conn = op.get_bind()

    if not _has_table(conn, "agentrun"):
        op.create_table(
        "agentrun",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("owner_subject", sa.String(), nullable=False, index=True),
        sa.Column(
            "mesh_agent_id",
            sa.String(),
            sa.ForeignKey("meshagent.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column(
            "mesh_task_id",
            sa.String(),
            sa.ForeignKey("meshtask.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        # claude_code | codex | gemini | custom | shell | …
        sa.Column("runtime_kind", sa.String(), nullable=False),
        sa.Column("runtime_session_id", sa.String(), nullable=True),
        # starting | running | paused | waiting_review | waiting_budget |
        # completed | failed | cancelled
        sa.Column("status", sa.String(), nullable=False, server_default="starting", index=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        # Random UUID minted on creation; used by the agent to resume after restart
        sa.Column("resume_token", sa.String(), nullable=False),
        sa.Column("last_checkpoint_at", sa.DateTime(), nullable=True),
        sa.Column("total_cost_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "parent_run_id",
            sa.String(),
            sa.ForeignKey("agentrun.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # Arbitrary JSON: tool call counts, model, custom agent metadata …
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )

    if not _has_table(conn, "runcheckpoint"):
        op.create_table(
            "runcheckpoint",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column(
                "run_id",
                sa.String(),
                sa.ForeignKey("agentrun.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            # Monotonically increasing within a run
            sa.Column("seq", sa.Integer(), nullable=False),
            # tool_call | turn | review | publish | manual
            sa.Column("kind", sa.String(), nullable=False),
            sa.Column("payload_json", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False, index=True),
            sa.UniqueConstraint("run_id", "seq", name="uq_runcheckpoint_run_seq"),
        )

    # Add agent_run_id FK to meshprogressevent (direct ops — avoid batch_alter_table
    # which can trigger a full table recreate from SQLModel metadata in newer alembic)
    if "agent_run_id" not in _col_set(conn, "meshprogressevent"):
        op.add_column(
            "meshprogressevent",
            sa.Column(
                "agent_run_id",
                sa.String(),
                sa.ForeignKey("agentrun.id", ondelete="SET NULL"),
                nullable=True,
            ),
        )
        op.create_index("ix_meshprogressevent_agent_run_id", "meshprogressevent", ["agent_run_id"])


def downgrade() -> None:
    with op.batch_alter_table("meshprogressevent") as batch_op:
        batch_op.drop_index("ix_meshprogressevent_agent_run_id")
        batch_op.drop_column("agent_run_id")

    op.drop_table("runcheckpoint")
    op.drop_table("agentrun")
