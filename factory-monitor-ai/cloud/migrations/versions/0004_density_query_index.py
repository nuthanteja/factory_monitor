"""density_snapshots query index — supports latest-per-zone reads

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-25
"""
from __future__ import annotations

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "idx_density_latest",
        "density_snapshots",
        ["camera_id", "zone_id", "ts"],
    )


def downgrade() -> None:
    op.drop_index("idx_density_latest", table_name="density_snapshots")
