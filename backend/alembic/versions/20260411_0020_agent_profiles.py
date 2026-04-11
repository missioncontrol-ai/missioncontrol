"""add agent profile, machine, and runtime json to mesh_agent

Revision ID: 20260411_0020
Revises: 20260410_0019
Create Date: 2026-04-11
"""

from alembic import op
import sqlalchemy as sa

revision = "20260411_0020"
down_revision = "20260410_0019"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("meshagent") as batch_op:
        batch_op.add_column(
            sa.Column("profile_json", sa.Text(), nullable=True, comment=(
                "User-defined agent profile: name, role, description, instructions, "
                "scope (directories/repos), permissions, constraints."
            ))
        )
        batch_op.add_column(
            sa.Column("machine_json", sa.Text(), nullable=True, comment=(
                "Auto-detected by mc-mesh at enrollment: hostname, os, cpu_cores, "
                "ram_gb, disk_free_gb, working_dir, installed_tools."
            ))
        )
        batch_op.add_column(
            sa.Column("runtime_json", sa.Text(), nullable=True, comment=(
                "Runtime metadata reported by mc-mesh: model, context_window, "
                "available_tools, extra version info."
            ))
        )


def downgrade():
    with op.batch_alter_table("meshagent") as batch_op:
        batch_op.drop_column("profile_json")
        batch_op.drop_column("machine_json")
        batch_op.drop_column("runtime_json")
