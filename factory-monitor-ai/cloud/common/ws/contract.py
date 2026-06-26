"""LOCKED WebSocket wire contract — design §5.5.

One canonical place that defines the versioned/sequenced envelope, the
Phase-2b WsType subset, and IncidentView. Both the /ws/live endpoint and the
Redis fan-out (slice ws-redis) build payloads from these models so the wire
shape can never drift. The TS mirror lives at frontend/src/lib/wsContract.ts.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum as PyEnum

from pydantic import BaseModel, ConfigDict, field_serializer, field_validator

WS_PROTOCOL_VERSION = 1


class WsType(str, PyEnum):
    # Phase-2b subset. detection.frame / whatsapp.message /
    # system.replay_mode are deferred (do NOT add here yet).
    SNAPSHOT = "snapshot"
    HEATMAP_TICK = "heatmap.tick"
    INCIDENT_CREATED = "incident.created"
    INCIDENT_UPDATED = "incident.updated"
    INCIDENT_TIER_ADVANCED = "incident.tier_advanced"
    INCIDENT_RESOLVED = "incident.resolved"
    TIMER_SNAPSHOT = "timer.snapshot"
    SYSTEM_HEARTBEAT = "system.heartbeat"


def _iso_z(dt: datetime) -> str:
    """ISO-8601 UTC with a trailing 'Z' (matches design §5.5 server_now)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


class IncidentView(BaseModel):
    """The per-incident projection the browser renders (design §5.5).

    deadline_at is the ABSOLUTE server deadline for the current tier; null when
    terminal. opened_at maps to Incident.created_at (no opened_at column exists).
    """

    model_config = ConfigDict(from_attributes=True)

    incident_id: str
    camera_id: str
    zone_id: str | None
    rule_id: str
    anomaly_type: str
    severity: str
    object_class: str | None
    status: str
    current_tier: int
    deadline_at: datetime | None
    opened_at: datetime
    snapshot_url: str | None
    tier_label: str

    @field_validator("status", "anomaly_type", "severity", mode="before")
    @classmethod
    def _enum_to_str(cls, v: object) -> object:
        return v.value if hasattr(v, "value") else v

    @field_validator("snapshot_url", mode="before")
    @classmethod
    def _empty_str_to_none(cls, v: object) -> object:
        return None if v == "" else v

    @field_serializer("deadline_at", "opened_at")
    def _ser_dt(self, dt: datetime | None) -> str | None:
        return None if dt is None else _iso_z(dt)


class WsEnvelope(BaseModel):
    type: WsType
    version: int = WS_PROTOCOL_VERSION
    seq: int
    server_now: str
    data: dict

    @field_serializer("type")
    def _ser_type(self, t: WsType) -> str:
        return t.value


def make_envelope(
    type: WsType,
    seq: int,
    data: dict,
    server_now: datetime | None = None,
) -> dict:
    """Build a wire-ready envelope dict (JSON-serializable values only)."""
    now = server_now if server_now is not None else datetime.now(tz=UTC)
    return WsEnvelope(
        type=type, seq=seq, server_now=_iso_z(now), data=data
    ).model_dump()
