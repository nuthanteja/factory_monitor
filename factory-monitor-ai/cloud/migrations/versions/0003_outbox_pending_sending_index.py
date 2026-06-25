"""outbox PENDING+SENDING partial index — supports the outbox_pending scrape gauge

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-24
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "idx_outbox_pending_sending",
        "outbox",
        ["status"],
        postgresql_where=sa.text("status IN ('PENDING','SENDING')"),
    )


def downgrade() -> None:
    op.drop_index("idx_outbox_pending_sending", table_name="outbox")
