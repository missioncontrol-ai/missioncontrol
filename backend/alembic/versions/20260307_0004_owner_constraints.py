"""enforce non-empty owners for mission and kluster

Revision ID: 20260307_0004
Revises: 20260306_0003
Create Date: 2026-03-07 20:10:00.000000
"""

from alembic import op
from sqlalchemy import inspect, text

# revision identifiers, used by Alembic.
revision = "20260307_0004"
down_revision = "20260306_0003"
branch_labels = None
depends_on = None


def _constraint_exists(conn, name: str) -> bool:
    row = conn.execute(
        text("SELECT 1 FROM pg_constraint WHERE conname = :name LIMIT 1"),
        {"name": name},
    ).first()
    return row is not None


def upgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name != "postgresql":
        return

    inspector = inspect(conn)
    tables = set(inspector.get_table_names())
    if "mission" in tables:
        conn.execute(
            text(
                "UPDATE mission "
                "SET owners = 'owner-required@system' "
                "WHERE btrim(COALESCE(owners, '')) = ''"
            )
        )
        if not _constraint_exists(conn, "ck_mission_owners_nonempty"):
            op.execute("ALTER TABLE mission ADD CONSTRAINT ck_mission_owners_nonempty CHECK (btrim(owners) <> '')")

    if "kluster" in tables:
        conn.execute(
            text(
                "UPDATE kluster "
                "SET owners = 'owner-required@system' "
                "WHERE btrim(COALESCE(owners, '')) = ''"
            )
        )
        if not _constraint_exists(conn, "ck_kluster_owners_nonempty"):
            op.execute("ALTER TABLE kluster ADD CONSTRAINT ck_kluster_owners_nonempty CHECK (btrim(owners) <> '')")


def downgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name != "postgresql":
        return

    if _constraint_exists(conn, "ck_mission_owners_nonempty"):
        op.execute("ALTER TABLE mission DROP CONSTRAINT ck_mission_owners_nonempty")
    if _constraint_exists(conn, "ck_kluster_owners_nonempty"):
        op.execute("ALTER TABLE kluster DROP CONSTRAINT ck_kluster_owners_nonempty")
