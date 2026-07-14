"""app state

Revision ID: 0002_app_state
Revises: 0001_initial
Create Date: 2026-07-14 00:00:01.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0002_app_state"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "app_state",
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )


def downgrade() -> None:
    op.drop_table("app_state")
