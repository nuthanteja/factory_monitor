"""initial schema — full spec §5.3

Revision ID: 0001
Revises:
Create Date: 2026-06-22
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

INCIDENT_STATUS = ("AWAITING_OPERATOR", "TIER1", "TIER2", "ACK", "RESOLVED", "CRITICAL_UNRESOLVED")
ANOMALY_TYPE = (
    "ppe_no_hardhat", "ppe_no_vest", "zone_intrusion", "loitering",
    "forklift_in_pedestrian_zone", "duty_zone_absence", "density_threshold",
)
ESCALATION_ROLE = ("OPERATOR", "FLOOR_MANAGER", "PLANT_DIRECTOR")
MSG_CHANNEL = ("whatsapp", "sms", "console")
MSG_DIRECTION = ("in", "out")
OUTBOX_STATUS = ("PENDING", "SENT", "DEAD")


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    # Create enums explicitly first
    incident_status_create = postgresql.ENUM(*INCIDENT_STATUS, name="incident_status")
    anomaly_type_create = postgresql.ENUM(*ANOMALY_TYPE, name="anomaly_type")
    escalation_role_create = postgresql.ENUM(*ESCALATION_ROLE, name="escalation_role")
    msg_channel_create = postgresql.ENUM(*MSG_CHANNEL, name="msg_channel")
    msg_direction_create = postgresql.ENUM(*MSG_DIRECTION, name="msg_direction")
    outbox_status_create = postgresql.ENUM(*OUTBOX_STATUS, name="outbox_status")
    bind = op.get_bind()
    for e in (incident_status_create, anomaly_type_create, escalation_role_create,
              msg_channel_create, msg_direction_create, outbox_status_create):
        e.create(bind, checkfirst=True)

    # create_type=False: enum already exists; don't let SA re-create it during create_table
    incident_status = postgresql.ENUM(*INCIDENT_STATUS, name="incident_status", create_type=False)
    anomaly_type = postgresql.ENUM(*ANOMALY_TYPE, name="anomaly_type", create_type=False)
    escalation_role = postgresql.ENUM(*ESCALATION_ROLE, name="escalation_role", create_type=False)
    msg_channel = postgresql.ENUM(*MSG_CHANNEL, name="msg_channel", create_type=False)
    msg_direction = postgresql.ENUM(*MSG_DIRECTION, name="msg_direction", create_type=False)
    outbox_status = postgresql.ENUM(*OUTBOX_STATUS, name="outbox_status", create_type=False)

    op.create_table(
        "incidents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("site_id", sa.Text(), nullable=False),
        sa.Column("camera_id", sa.Text(), nullable=False),
        sa.Column("zone_id", sa.Text()),
        sa.Column("anomaly_type", anomaly_type, nullable=False),
        sa.Column("rule_id", sa.Text(), nullable=False),
        sa.Column("object_class", sa.Text()),
        sa.Column("track_id", sa.Text()),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("dedup_key", sa.Text(), nullable=False),
        sa.Column("status", incident_status, nullable=False, server_default="AWAITING_OPERATOR"),
        sa.Column("current_tier", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_fire_at", sa.DateTime(timezone=True)),
        sa.Column("deadline_at", sa.DateTime(timezone=True)),
        sa.Column("claimed_by", sa.Text()),
        sa.Column("claimed_until", sa.DateTime(timezone=True)),
        sa.Column("snapshot_url", sa.Text()),
        sa.Column("acked_by", postgresql.UUID(as_uuid=True)),
        sa.Column("acked_at", sa.DateTime(timezone=True)),
        sa.Column("resolved_by", postgresql.UUID(as_uuid=True)),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("resolution_note", sa.Text()),
        sa.Column("is_synthetic", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "uq_incident_open_dedup", "incidents", ["dedup_key"], unique=True,
        postgresql_where=sa.text("status IN ('AWAITING_OPERATOR','TIER1','TIER2')"),
    )
    op.create_index(
        "idx_incident_due", "incidents", ["next_fire_at"],
        postgresql_where=sa.text("status IN ('AWAITING_OPERATOR','TIER1','TIER2')"),
    )

    op.create_table(
        "incident_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("incident_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("incidents.id"), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("from_state", sa.Text()),
        sa.Column("to_state", sa.Text()),
        sa.Column("tier", sa.Integer()),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True)),
        sa.Column("source_event_id", postgresql.UUID(as_uuid=True)),
        sa.Column("payload", postgresql.JSONB()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "uq_event_source", "incident_events", ["source_event_id"], unique=True,
        postgresql_where=sa.text("source_event_id IS NOT NULL"),
    )
    op.create_index("idx_events_incident", "incident_events", ["incident_id", "created_at"])

    op.create_table(
        "escalation_idempotency",
        sa.Column("incident_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("incidents.id"), primary_key=True),
        sa.Column("tier", sa.Integer(), primary_key=True),
        sa.Column(
            "fired_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )

    op.create_table(
        "outbox",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("incident_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("incidents.id")),
        sa.Column("tier", sa.Integer()),
        sa.Column("to_phone_e164", sa.Text(), nullable=False),
        sa.Column("channel", msg_channel, nullable=False, server_default="whatsapp"),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("template_name", sa.Text()),
        sa.Column("variables", postgresql.JSONB()),
        sa.Column("body", sa.Text()),
        sa.Column("idempotency_key", sa.Text(), unique=True),
        sa.Column("status", outbox_status, nullable=False, server_default="PENDING"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="6"),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(),
        ),
        sa.Column("provider_sid", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "idx_outbox_due", "outbox", ["next_attempt_at"],
        postgresql_where=sa.text("status = 'PENDING'"),
    )

    op.create_table(
        "messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("incident_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("incidents.id")),
        sa.Column("direction", msg_direction, nullable=False),
        sa.Column("channel", msg_channel, nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True)),
        sa.Column("to_phone_e164", sa.Text()),
        sa.Column("from_phone_e164", sa.Text()),
        sa.Column("body", sa.Text()),
        sa.Column("provider_sid", sa.Text()),
        sa.Column("status", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )

    op.create_table(
        "whatsapp_sessions",
        sa.Column("phone_e164", sa.Text(), primary_key=True),
        sa.Column("window_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "last_inbound_at",
            sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "unmatched_inbound",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("from_phone_e164", sa.Text(), nullable=False),
        sa.Column("body", sa.Text()),
        sa.Column("provider_sid", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("site_id", sa.Text(), nullable=False),
        sa.Column("full_name", sa.Text(), nullable=False),
        sa.Column("phone_e164", sa.Text(), nullable=False),
        sa.Column("email", sa.Text()),
        sa.Column("password_hash", sa.Text()),
        sa.Column("role", sa.Text(), nullable=False, server_default="VIEWER"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )

    op.create_table(
        "on_call_assignments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("site_id", sa.Text(), nullable=False),
        sa.Column("role", escalation_role, nullable=False),
        sa.Column("zone_id", sa.Text()),
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("idx_oncall_lookup", "on_call_assignments",
                    ["role", "zone_id", "starts_at", "ends_at"])

    op.create_table(
        "escalation_tiers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("site_id", sa.Text(), nullable=False),
        sa.Column("anomaly_type", anomaly_type),
        sa.Column("tier", sa.Integer(), nullable=False),
        sa.Column("role", escalation_role, nullable=False),
        sa.Column("delay_seconds", sa.Integer(), nullable=False),
        sa.Column("template_name", sa.Text(), nullable=False),
        sa.UniqueConstraint("site_id", "anomaly_type", "tier", name="uq_escalation_tier"),
    )

    op.create_table(
        "zones",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("site_id", sa.Text(), nullable=False),
        sa.Column("camera_id", sa.Text()),
        sa.Column("name", sa.Text()),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("polygon", postgresql.JSONB(), nullable=False),
        sa.Column("required_ppe", postgresql.ARRAY(sa.String()), server_default=sa.text("'{}'")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )

    op.create_table(
        "cameras",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("site_id", sa.Text(), nullable=False),
        sa.Column("edge_id", sa.Text()),
        sa.Column("name", sa.Text()),
        sa.Column("rtsp_path", sa.Text(), nullable=False),
        sa.Column("whep_url", sa.Text()),
        sa.Column("zone_id", sa.Text(), sa.ForeignKey("zones.id")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )

    op.create_table(
        "density_snapshots",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("camera_id", sa.Text()),
        sa.Column("zone_id", sa.Text()),
        sa.Column("count", sa.Integer()),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    for tbl in (
        "density_snapshots", "cameras", "zones", "escalation_tiers",
        "on_call_assignments", "users", "unmatched_inbound", "whatsapp_sessions",
        "messages", "outbox", "escalation_idempotency", "incident_events", "incidents",
    ):
        op.drop_table(tbl)

    bind = op.get_bind()
    for name in ("outbox_status", "msg_direction", "msg_channel",
                 "escalation_role", "anomaly_type", "incident_status"):
        postgresql.ENUM(name=name).drop(bind, checkfirst=True)
