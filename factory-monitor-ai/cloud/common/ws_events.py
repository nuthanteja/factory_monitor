"""Publish-side change-event vocabulary + compact (de)serialisation for the
Redis live fan-out (design §3.2, §5.5).

Writers publish a COMPACT change event ({change_type, incident_id, +minimal
hints}) — NOT a full IncidentView. The WS subscriber re-reads the incident
from Postgres to build the authoritative view, so the channel only needs to
carry the id + what changed. See slice-2 design rationale.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime

# change_type vocabulary — mirrors the LOCKED §5.5 WsType strings.
CHANGE_CREATED = "incident.created"
CHANGE_UPDATED = "incident.updated"
CHANGE_TIER_ADVANCED = "incident.tier_advanced"
CHANGE_RESOLVED = "incident.resolved"


def incident_change(
    change_type: str, incident_id: uuid.UUID, **fields: object
) -> dict:
    """Build a compact change event for the Redis channel.

    Always carries change_type + incident_id (stringified). Optional **fields
    are minimal hints (current_tier, status, deadline_at, resolved_at, ...);
    the subscriber still re-reads the row for the authoritative IncidentView.
    """
    change: dict = {"change_type": change_type, "incident_id": str(incident_id)}
    change.update(fields)
    return change


def _default(o: object) -> str:
    if isinstance(o, datetime):
        return o.isoformat()
    if isinstance(o, uuid.UUID):
        return str(o)
    raise TypeError(f"Object of type {type(o).__name__} is not JSON serialisable")


def encode_change(change: dict) -> str:
    """Serialise a change dict to a compact JSON string (UUID/datetime safe)."""
    return json.dumps(change, separators=(",", ":"), default=_default)


def decode_change(raw: str | bytes) -> dict:
    """Parse a JSON change string/bytes (redis returns bytes) back to a dict."""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    return json.loads(raw)
