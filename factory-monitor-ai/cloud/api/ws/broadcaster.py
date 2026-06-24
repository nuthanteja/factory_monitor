"""Translate a compact Redis change-event into a live WS broadcast (design §5.5).

Thin adapter layer for the /ws/live fan-out path: re-reads the incident,
builds the §5.5 envelope (via make_envelope), and passes the completed dict
to manager.broadcast(envelope) so the manager only needs to forward bytes.

This differs from cloud.common.ws.broadcaster, which delegates envelope
framing to ConnectionManager.broadcast(WsType, data). Here the envelope is
built before the manager is involved, matching the RedisFanoutSubscriber's
ConnectionManagerLike protocol (next_seq + broadcast(dict)).
"""
from __future__ import annotations

import uuid
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from cloud.common.db.models import Incident
from cloud.common.ws.contract import WsType, make_envelope
from cloud.common.ws.incident_view import build_incident_view
from cloud.common.ws_events import (
    CHANGE_CREATED,
    CHANGE_RESOLVED,
    CHANGE_TIER_ADVANCED,
    CHANGE_UPDATED,
)


class ConnectionManagerLike(Protocol):
    """Minimal interface the broadcaster requires from a manager."""

    def next_seq(self) -> int: ...

    async def broadcast(self, envelope: dict) -> None: ...


def _iso(dt: object) -> str | None:  # type: ignore[return]
    from datetime import datetime

    if isinstance(dt, datetime):
        return dt.isoformat()
    return None


async def _read_incident(
    session_maker: async_sessionmaker, incident_id: uuid.UUID
) -> Incident | None:
    async with session_maker() as s:
        return (
            await s.execute(select(Incident).where(Incident.id == incident_id))
        ).scalar_one_or_none()


def _build_data(change_type: str, inc: Incident) -> dict:
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
    return build_incident_view(inc).model_dump()


async def broadcast_change(
    session_maker: async_sessionmaker,
    manager: ConnectionManagerLike,
    change: dict,
) -> bool:
    """Re-read the incident, build a §5.5 envelope, and call manager.broadcast(envelope).

    Returns True if a broadcast was issued; False if the incident no longer exists.
    """
    change_type: str = change["change_type"]
    incident_id = uuid.UUID(str(change["incident_id"]))

    inc = await _read_incident(session_maker, incident_id)
    if inc is None:
        return False

    try:
        ws_type = WsType(change_type)
    except ValueError:
        ws_type = WsType.INCIDENT_UPDATED

    data = _build_data(change_type, inc)
    seq = manager.next_seq()
    envelope = make_envelope(ws_type, seq=seq, data=data)
    await manager.broadcast(envelope)
    return True
