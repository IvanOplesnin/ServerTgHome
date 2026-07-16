"""audio message artifacts

Revision ID: 0004_audio_messages
Revises: 0003_sensor_readings
Create Date: 2026-07-16 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0004_audio_messages"
down_revision = "0003_sensor_readings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audio_messages",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("camera_id", sa.String(length=128), nullable=False),
        sa.Column("source_path", sa.Text(), nullable=False),
        sa.Column("prepared_path", sa.Text(), nullable=True),
        sa.Column("source_size_bytes", sa.Integer(), nullable=False),
        sa.Column("prepared_size_bytes", sa.Integer(), nullable=True),
        sa.Column("duration_sec", sa.Integer(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audio_messages_camera_id", "audio_messages", ["camera_id"])
    op.create_index("ix_audio_messages_job_id", "audio_messages", ["job_id"])


def downgrade() -> None:
    op.drop_index("ix_audio_messages_job_id", table_name="audio_messages")
    op.drop_index("ix_audio_messages_camera_id", table_name="audio_messages")
    op.drop_table("audio_messages")
