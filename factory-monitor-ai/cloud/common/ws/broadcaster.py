"""Translate a compact Redis change-event into a live WS broadcast (design §5.5).

The compact event only carries {change_type, incident_id, +minimal hints}.
This module re-reads the incident from Postgres (the single source of truth),
builds the authoritative IncidentView via build_incident_view, maps the
change_type string to the corresponding WsType, and calls
manager.broadcast(ws_type, data) — the ConnectionManager owns envelope framing
and per-connection seq assignment (broadcaster does NOT build the envelope).
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from cloud.common.db.models import Incident
from cloud.common.ws.contract import WsType
from cloud.common.ws.incident_view import build_incident_view
from cloud.common.ws_events import (
    CHANGE_CREATED,
    CHANGE_RESOLVED,
    CHANGE_TIER_ADVANCED,
    CHANGE_UPDATED,
)

# These change_type strings ARE the WsType .value strings — direct coercion works.
_KNOWN_CHANGE_TYPES: frozenset[str] = frozenset(
    (CHANGE_CREATED, CHANGE_UPDATED, CHANGE_TIER_ADVANCED, CHANGE_RESOLVED)
)


async def _read_incident(
    session_maker: async_sessionmaker, incident_id: uuid.UUID
) -> Incident | None:
    async with session_maker() as s:
        return (
            await s.execute(select(Incident).where(Incident.id == incident_id))
        ).scalar_one_or_none()


def _iso(dt: object) -> str | None:  # type: ignore[return]
    from datetime import datetime

    if isinstance(dt, datetime):
        return dt.isoformat()
    return None


def _build_data(change_type: str, inc: Incident) -> dict:
    """Build the data payload for a given change_type from a freshly-read incident."""
    if change_type in (CHANGE_CREATED, CHANGE_UPDATED):
        return build_incident_view(inc).model_dump()
    if change_type == CHANGE_TIER_ADVANCED:
        return {
            "incident_id": str(inc.id),
            "current_tier": inc.current_tier,
            "status": inc.status.value if hasattr(inc.status, "value") else str(inc.status),
            "deadline_at": _iso(inc.deadline_at),
        }
    if change_type == CHANGE_RESOLVED:
        return {
            "incident_id": str(inc.id),
            "resolved_at": _iso(inc.resolved_at),
            "resolved_by": str(inc.resolved_by) if inc.resolved_by else None,
        }
    # Unknown change_type: fall back to a full updated view so the UI re-syncs.
    return build_incident_view(inc).model_dump()


async def broadcast_change(
    session_maker: async_sessionmaker,
    manager: object,  # ConnectionManager (or any object with broadcast(WsType, dict)->int)
    change: dict,
) -> int:
    """Re-read the incident, build the data payload, and fan-out via manager.

    Returns the number of connections the message was sent to (0 if the
    incident no longer exists or no clients are connected).
    """
    change_type: str = change["change_type"]
    incident_id = uuid.UUID(str(change["incident_id"]))

    inc = await _read_incident(session_maker, incident_id)
    if inc is None:
        return 0

    # change_type strings are exactly the WsType .value strings.
    try:
        ws_type = WsType(change_type)
    except ValueError:
        ws_type = WsType.INCIDENT_UPDATED

    data = _build_data(change_type, inc)
    return await manager.broadcast(ws_type, data)
