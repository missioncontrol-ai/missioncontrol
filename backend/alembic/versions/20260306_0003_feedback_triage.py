"""feedback triage fields

Revision ID: 20260306_0003
Revises: 20260301_0002
Create Date: 2026-03-06 20:40:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision = "20260306_0003"
down_revision = "20260301_0002"
branch_labels = None
depends_on = None


def _ensure_column(table_name: str, column: sa.Column) -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    existing_columns = {item.get("name") for item in inspector.get_columns(table_name)}
    if column.name not in existing_columns:
        op.add_column(table_name, column)


def _ensure_index(table_name: str, index_name: str, columns: list[str]) -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    existing_indexes = {idx.get("name") for idx in inspector.get_indexes(table_name)}
    if index_name not in existing_indexes:
        op.create_index(index_name, table_name, columns)


def upgrade() -> None:
    _ensure_column("feedbackentry", sa.Column("triage_status", sa.String(), nullable=False, server_default="new"))
    _ensure_column("feedbackentry", sa.Column("priority", sa.String(), nullable=False, server_default="p2"))
    _ensure_column("feedbackentry", sa.Column("owner", sa.String(), nullable=False, server_default=""))
    _ensure_column("feedbackentry", sa.Column("disposition", sa.String(), nullable=False, server_default=""))
    _ensure_column("feedbackentry", sa.Column("outcome_ref", sa.String(), nullable=False, server_default=""))

    _ensure_index("feedbackentry", "ix_feedbackentry_triage_status", ["triage_status"])
    _ensure_index("feedbackentry", "ix_feedbackentry_priority", ["priority"])
    _ensure_index("feedbackentry", "ix_feedbackentry_owner", ["owner"])
    _ensure_index("feedbackentry", "ix_feedbackentry_disposition", ["disposition"])


def downgrade() -> None:
    # Intentionally no-op for SQLite compatibility in downgrade cycles.
    # Baseline downgrade drops all tables at base.
    pass
