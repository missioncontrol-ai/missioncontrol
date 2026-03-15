"""add mission-scoped persistence tables

Revision ID: 20260315_0014
Revises: 20260315_0013
Create Date: 2026-03-15 16:40:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "20260315_0014"
down_revision = "20260315_0013"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    conn = op.get_bind()
    from sqlalchemy import inspect as _inspect

    return name in _inspect(conn).get_table_names()


def upgrade() -> None:
    if not _has_table("repoconnection"):
        op.create_table(
            "repoconnection",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("owner_subject", sa.String(), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("provider", sa.String(), nullable=False),
            sa.Column("host", sa.String(), nullable=False, server_default="github.com"),
            sa.Column("repo_path", sa.String(), nullable=False, server_default=""),
            sa.Column("default_branch", sa.String(), nullable=False, server_default="main"),
            sa.Column("credential_ref", sa.String(), nullable=False, server_default=""),
            sa.Column("options_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("owner_subject", "name", name="uq_repo_connection_owner_name"),
        )
        op.create_index("ix_repoconnection_owner_subject", "repoconnection", ["owner_subject"])
        op.create_index("ix_repoconnection_name", "repoconnection", ["name"])
        op.create_index("ix_repoconnection_provider", "repoconnection", ["provider"])
        op.create_index("ix_repoconnection_host", "repoconnection", ["host"])

    if not _has_table("repobinding"):
        op.create_table(
            "repobinding",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("owner_subject", sa.String(), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("connection_id", sa.Integer(), nullable=False),
            sa.Column("branch_override", sa.String(), nullable=False, server_default=""),
            sa.Column("base_path", sa.String(), nullable=False, server_default="missions"),
            sa.Column("active", sa.Boolean(), nullable=False, server_default="1"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("owner_subject", "name", name="uq_repo_binding_owner_name"),
        )
        op.create_index("ix_repobinding_owner_subject", "repobinding", ["owner_subject"])
        op.create_index("ix_repobinding_name", "repobinding", ["name"])
        op.create_index("ix_repobinding_connection_id", "repobinding", ["connection_id"])
        op.create_index("ix_repobinding_active", "repobinding", ["active"])

    if not _has_table("missionpersistencepolicy"):
        op.create_table(
            "missionpersistencepolicy",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("mission_id", sa.String(), nullable=False),
            sa.Column("default_binding_id", sa.Integer(), nullable=True),
            sa.Column("fallback_mode", sa.String(), nullable=False, server_default="fail_closed"),
            sa.Column("require_approval", sa.Boolean(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("mission_id", name="uq_mission_persistence_policy_mission"),
        )
        op.create_index("ix_missionpersistencepolicy_mission_id", "missionpersistencepolicy", ["mission_id"])
        op.create_index(
            "ix_missionpersistencepolicy_default_binding_id",
            "missionpersistencepolicy",
            ["default_binding_id"],
        )
        op.create_index("ix_missionpersistencepolicy_fallback_mode", "missionpersistencepolicy", ["fallback_mode"])
        op.create_index(
            "ix_missionpersistencepolicy_require_approval",
            "missionpersistencepolicy",
            ["require_approval"],
        )

    if not _has_table("missionpersistenceroute"):
        op.create_table(
            "missionpersistenceroute",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("mission_id", sa.String(), nullable=False),
            sa.Column("entity_kind", sa.String(), nullable=False),
            sa.Column("event_kind", sa.String(), nullable=False, server_default=""),
            sa.Column("binding_id", sa.Integer(), nullable=False),
            sa.Column("branch_override", sa.String(), nullable=False, server_default=""),
            sa.Column(
                "path_template",
                sa.String(),
                nullable=False,
                server_default="missions/{mission_id}/{entity_kind}/{entity_id}.json",
            ),
            sa.Column("format", sa.String(), nullable=False, server_default="json_v1"),
            sa.Column("active", sa.Boolean(), nullable=False, server_default="1"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_missionpersistenceroute_mission_id", "missionpersistenceroute", ["mission_id"])
        op.create_index("ix_missionpersistenceroute_entity_kind", "missionpersistenceroute", ["entity_kind"])
        op.create_index("ix_missionpersistenceroute_event_kind", "missionpersistenceroute", ["event_kind"])
        op.create_index("ix_missionpersistenceroute_binding_id", "missionpersistenceroute", ["binding_id"])
        op.create_index("ix_missionpersistenceroute_active", "missionpersistenceroute", ["active"])

    if not _has_table("publicationrecord"):
        op.create_table(
            "publicationrecord",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("owner_subject", sa.String(), nullable=False),
            sa.Column("mission_id", sa.String(), nullable=True),
            sa.Column("ledger_event_id", sa.Integer(), nullable=True),
            sa.Column("entity_kind", sa.String(), nullable=False),
            sa.Column("entity_id", sa.String(), nullable=False),
            sa.Column("event_kind", sa.String(), nullable=False),
            sa.Column("binding_id", sa.Integer(), nullable=False),
            sa.Column("repo_url", sa.String(), nullable=False, server_default=""),
            sa.Column("branch", sa.String(), nullable=False, server_default=""),
            sa.Column("file_path", sa.String(), nullable=False, server_default=""),
            sa.Column("commit_sha", sa.String(), nullable=False, server_default=""),
            sa.Column("status", sa.String(), nullable=False, server_default="succeeded"),
            sa.Column("error", sa.Text(), nullable=False, server_default=""),
            sa.Column("detail_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_publicationrecord_owner_subject", "publicationrecord", ["owner_subject"])
        op.create_index("ix_publicationrecord_mission_id", "publicationrecord", ["mission_id"])
        op.create_index("ix_publicationrecord_ledger_event_id", "publicationrecord", ["ledger_event_id"])
        op.create_index("ix_publicationrecord_entity_kind", "publicationrecord", ["entity_kind"])
        op.create_index("ix_publicationrecord_entity_id", "publicationrecord", ["entity_id"])
        op.create_index("ix_publicationrecord_event_kind", "publicationrecord", ["event_kind"])
        op.create_index("ix_publicationrecord_binding_id", "publicationrecord", ["binding_id"])
        op.create_index("ix_publicationrecord_commit_sha", "publicationrecord", ["commit_sha"])
        op.create_index("ix_publicationrecord_status", "publicationrecord", ["status"])


def downgrade() -> None:
    op.drop_table("publicationrecord")
    op.drop_table("missionpersistenceroute")
    op.drop_table("missionpersistencepolicy")
    op.drop_table("repobinding")
    op.drop_table("repoconnection")
