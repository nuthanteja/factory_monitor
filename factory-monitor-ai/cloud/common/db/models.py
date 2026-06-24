"""SQLAlchemy ORM models — full spec §5.3 source-of-truth schema."""
from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from cloud.common.db.base import Base


class IncidentStatus(str, PyEnum):
    AWAITING_OPERATOR = "AWAITING_OPERATOR"
    TIER1 = "TIER1"
    TIER2 = "TIER2"
    ACK = "ACK"
    RESOLVED = "RESOLVED"
    CRITICAL_UNRESOLVED = "CRITICAL_UNRESOLVED"


# PG enum bound to the IncidentStatus python enum; uses the enum *values*.
incident_status_enum = Enum(
    IncidentStatus,
    name="incident_status",
    values_callable=lambda e: [m.value for m in e],
)
anomaly_type_enum = Enum(
    "ppe_no_hardhat", "ppe_no_vest", "zone_intrusion", "loitering",
    "forklift_in_pedestrian_zone", "duty_zone_absence", "density_threshold",
    name="anomaly_type",
)
escalation_role_enum = Enum(
    "OPERATOR", "FLOOR_MANAGER", "PLANT_DIRECTOR", name="escalation_role",
)
msg_channel_enum = Enum("whatsapp", "sms", "console", name="msg_channel")
msg_direction_enum = Enum("in", "out", name="msg_direction")
outbox_status_enum = Enum("PENDING", "SENDING", "SENT", "DEAD", name="outbox_status")


class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    site_id: Mapped[str] = mapped_column(Text, nullable=False)
    camera_id: Mapped[str] = mapped_column(Text, nullable=False)
    zone_id: Mapped[str | None] = mapped_column(Text)
    anomaly_type: Mapped[str] = mapped_column(anomaly_type_enum, nullable=False)
    rule_id: Mapped[str] = mapped_column(Text, nullable=False)
    object_class: Mapped[str | None] = mapped_column(Text)
    track_id: Mapped[str | None] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    dedup_key: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[IncidentStatus] = mapped_column(
        incident_status_enum, nullable=False, default=IncidentStatus.AWAITING_OPERATOR,
        server_default="AWAITING_OPERATOR",
    )
    current_tier: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    next_fire_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claimed_by: Mapped[str | None] = mapped_column(Text)
    claimed_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    snapshot_url: Mapped[str | None] = mapped_column(Text)
    acked_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    acked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_note: Mapped[str | None] = mapped_column(Text)
    is_synthetic: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index(
            "uq_incident_open_dedup", "dedup_key", unique=True,
            postgresql_where=text("status IN ('AWAITING_OPERATOR','TIER1','TIER2')"),
        ),
        Index(
            "idx_incident_due", "next_fire_at",
            postgresql_where=text("status IN ('AWAITING_OPERATOR','TIER1','TIER2')"),
        ),
    )


class IncidentEvent(Base):
    __tablename__ = "incident_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    incident_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("incidents.id"), nullable=False
    )
    type: Mapped[str] = mapped_column(Text, nullable=False)
    from_state: Mapped[str | None] = mapped_column(Text)
    to_state: Mapped[str | None] = mapped_column(Text)
    tier: Mapped[int | None] = mapped_column(Integer)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    source_event_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    payload: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index(
            "uq_event_source", "source_event_id", unique=True,
            postgresql_where=text("source_event_id IS NOT NULL"),
        ),
        Index("idx_events_incident", "incident_id", "created_at"),
    )


class EscalationIdempotency(Base):
    __tablename__ = "escalation_idempotency"

    incident_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("incidents.id"), primary_key=True
    )
    tier: Mapped[int] = mapped_column(Integer, primary_key=True)
    fired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Outbox(Base):
    __tablename__ = "outbox"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    incident_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("incidents.id")
    )
    tier: Mapped[int | None] = mapped_column(Integer)
    to_phone_e164: Mapped[str] = mapped_column(Text, nullable=False)
    channel: Mapped[str] = mapped_column(
        msg_channel_enum, nullable=False, default="whatsapp", server_default="whatsapp"
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    template_name: Mapped[str | None] = mapped_column(Text)
    variables: Mapped[dict | None] = mapped_column(JSONB)
    body: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str | None] = mapped_column(Text, unique=True)
    status: Mapped[str] = mapped_column(
        outbox_status_enum, nullable=False, default="PENDING", server_default="PENDING"
    )
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=6, server_default="6"
    )
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    provider_sid: Mapped[str | None] = mapped_column(Text)
    claimed_by: Mapped[str | None] = mapped_column(Text)
    claimed_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index(
            "idx_outbox_due", "next_attempt_at",
            postgresql_where=text("status = 'PENDING'"),
        ),
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    incident_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("incidents.id")
    )
    direction: Mapped[str] = mapped_column(msg_direction_enum, nullable=False)
    channel: Mapped[str] = mapped_column(msg_channel_enum, nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    to_phone_e164: Mapped[str | None] = mapped_column(Text)
    from_phone_e164: Mapped[str | None] = mapped_column(Text)
    body: Mapped[str | None] = mapped_column(Text)
    provider_sid: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class WhatsappSession(Base):
    __tablename__ = "whatsapp_sessions"

    phone_e164: Mapped[str] = mapped_column(Text, primary_key=True)
    window_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_inbound_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class UnmatchedInbound(Base):
    __tablename__ = "unmatched_inbound"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    from_phone_e164: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str | None] = mapped_column(Text)
    provider_sid: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    site_id: Mapped[str] = mapped_column(Text, nullable=False)
    full_name: Mapped[str] = mapped_column(Text, nullable=False)
    phone_e164: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[str | None] = mapped_column(Text)
    password_hash: Mapped[str | None] = mapped_column(Text)
    role: Mapped[str] = mapped_column(
        Text, nullable=False, default="VIEWER", server_default="VIEWER"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class OnCallAssignment(Base):
    __tablename__ = "on_call_assignments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    site_id: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(escalation_role_enum, nullable=False)
    zone_id: Mapped[str | None] = mapped_column(Text)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("idx_oncall_lookup", "role", "zone_id", "starts_at", "ends_at"),
    )


class EscalationTier(Base):
    __tablename__ = "escalation_tiers"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    site_id: Mapped[str] = mapped_column(Text, nullable=False)
    anomaly_type: Mapped[str | None] = mapped_column(anomaly_type_enum)
    tier: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(escalation_role_enum, nullable=False)
    delay_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    template_name: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint("site_id", "anomaly_type", "tier", name="uq_escalation_tier"),
    )


class Zone(Base):
    __tablename__ = "zones"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    site_id: Mapped[str] = mapped_column(Text, nullable=False)
    camera_id: Mapped[str | None] = mapped_column(Text)
    name: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    polygon: Mapped[dict] = mapped_column(JSONB, nullable=False)
    required_ppe: Mapped[list[str]] = mapped_column(
        ARRAY(String), server_default=text("'{}'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Camera(Base):
    __tablename__ = "cameras"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    site_id: Mapped[str] = mapped_column(Text, nullable=False)
    edge_id: Mapped[str | None] = mapped_column(Text)
    name: Mapped[str | None] = mapped_column(Text)
    rtsp_path: Mapped[str] = mapped_column(Text, nullable=False)
    whep_url: Mapped[str | None] = mapped_column(Text)
    zone_id: Mapped[str | None] = mapped_column(Text, ForeignKey("zones.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DensitySnapshot(Base):
    __tablename__ = "density_snapshots"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    camera_id: Mapped[str | None] = mapped_column(Text)
    zone_id: Mapped[str | None] = mapped_column(Text)
    count: Mapped[int | None] = mapped_column(Integer)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
