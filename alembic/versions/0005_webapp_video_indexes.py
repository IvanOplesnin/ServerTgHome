"""optimize the Mini App video archive

Revision ID: 0005_webapp_video_indexes
Revises: 0004_audio_messages
Create Date: 2026-07-27 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0005_webapp_video_indexes"
down_revision = "0004_audio_messages"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("videos") as batch_op:
        batch_op.alter_column(
            "size_bytes",
            existing_type=sa.Integer(),
            type_=sa.BigInteger(),
            existing_nullable=False,
        )

    active_videos = sa.text("deleted_at IS NULL")
    op.create_index(
        "ix_videos_active_created_id",
        "videos",
        ["created_at", "id"],
        unique=False,
        postgresql_where=active_videos,
        sqlite_where=active_videos,
    )
    op.create_index(
        "ix_videos_active_camera_created_id",
        "videos",
        ["camera_id", "created_at", "id"],
        unique=False,
        postgresql_where=active_videos,
        sqlite_where=active_videos,
    )


def downgrade() -> None:
    op.drop_index("ix_videos_active_camera_created_id", table_name="videos")
    op.drop_index("ix_videos_active_created_id", table_name="videos")

    with op.batch_alter_table("videos") as batch_op:
        batch_op.alter_column(
            "size_bytes",
            existing_type=sa.BigInteger(),
            type_=sa.Integer(),
            existing_nullable=False,
        )
