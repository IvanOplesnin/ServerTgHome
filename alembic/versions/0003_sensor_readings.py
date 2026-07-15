"""sensor readings history

Revision ID: 0003_sensor_readings
Revises: 0002_app_state
Create Date: 2026-07-15 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0003_sensor_readings"
down_revision = "0002_app_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sensor_readings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("room_id", sa.String(length=128), nullable=False),
        sa.Column("metric", sa.String(length=32), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("unit", sa.String(length=16), nullable=False),
        sa.Column("entity_id", sa.Text(), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sensor_readings_room_id", "sensor_readings", ["room_id"])
    op.create_index("ix_sensor_readings_metric", "sensor_readings", ["metric"])
    op.create_index("ix_sensor_readings_recorded_at", "sensor_readings", ["recorded_at"])
    op.create_index("ix_sensor_readings_received_at", "sensor_readings", ["received_at"])
    op.create_index(
        "ix_sensor_readings_room_metric_recorded",
        "sensor_readings",
        ["room_id", "metric", "recorded_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_sensor_readings_room_metric_recorded", table_name="sensor_readings")
    op.drop_index("ix_sensor_readings_received_at", table_name="sensor_readings")
    op.drop_index("ix_sensor_readings_recorded_at", table_name="sensor_readings")
    op.drop_index("ix_sensor_readings_metric", table_name="sensor_readings")
    op.drop_index("ix_sensor_readings_room_id", table_name="sensor_readings")
    op.drop_table("sensor_readings")
