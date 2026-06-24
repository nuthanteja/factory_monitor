"""outbox two-phase SENDING claim — Phase 3a exactly-once send

Adds:
  - 'SENDING' value to the outbox_status enum
  - outbox.claimed_by / outbox.claimed_until (the send lease)
  - idx_outbox_sending_reclaim (partial index for the reclaim subquery)

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-24
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE cannot run inside the migration's transaction on all
    # PG versions; run it in an autocommit block. IF NOT EXISTS makes it re-runnable.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE outbox_status ADD VALUE IF NOT EXISTS 'SENDING'")

    op.add_column("outbox", sa.Column("claimed_by", sa.Text(), nullable=True))
    op.add_column("outbox", sa.Column("claimed_until", sa.DateTime(timezone=True), nullable=True))

    # Supports the reclaim arm of the claim subquery:
    #   ... OR (status = 'SENDING' AND claimed_until < now())
    op.create_index(
        "idx_outbox_sending_reclaim",
        "outbox",
        ["claimed_until"],
        postgresql_where=sa.text("status = 'SENDING'"),
    )


def downgrade() -> None:
    op.drop_index("idx_outbox_sending_reclaim", table_name="outbox")
    op.drop_column("outbox", "claimed_until")
    op.drop_column("outbox", "claimed_by")
    # NOTE: the 'SENDING' enum value is intentionally retained — Postgres cannot
    # DROP VALUE without a full type rebuild, and an unused enum value is harmless.
