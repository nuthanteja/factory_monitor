"""Shared incident-action service: acknowledge and resolve.

Both the HTTP API routes (cloud.api.routes) and the e2e test suite call these
functions directly so they always exercise exactly the same state-machine logic.

Status guards and field-setting EXACTLY match what was in routes.py before the
extraction — no behaviour change.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cloud.common.db.models import Incident, IncidentEvent, IncidentStatus

_RESOLVABLE_FROM = {
    IncidentStatus.AWAITING_OPERATOR,
    IncidentStatus.TIER1,
    IncidentStatus.TIER2,
    IncidentStatus.ACK,
}

_ACKABLE_FROM = {
    IncidentStatus.AWAITING_OPERATOR,
    IncidentStatus.TIER1,
    IncidentStatus.TIER2,
}


async def acknowledge_incident(
    session: AsyncSession,
    incident_id: uuid.UUID,
    *,
    idempotency_key: str | None = None,
) -> Incident:
    """Set incident status → ACK, clear next_fire_at/deadline_at, write ACK audit row.

    Idempotent: if the incident is already ACK returns the row unchanged.
    Raises HTTPException(404) if not found, HTTPException(409) if status invalid.
    The caller is responsible for committing the session.
    """
    inc = (
        await session.execute(select(Incident).where(Incident.id == incident_id))
    ).scalar_one_or_none()

    if inc is None:
        raise HTTPException(status_code=404, detail="Incident not found")

    if inc.status == IncidentStatus.ACK:
        # Already acknowledged — idempotent return
        return inc

    if inc.status not in _ACKABLE_FROM:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot acknowledge incident in status {inc.status.value}",
        )

    now = datetime.now(tz=UTC)
    prev_status = inc.status.value
    inc.status = IncidentStatus.ACK
    inc.next_fire_at = None
    inc.deadline_at = None
    inc.acked_at = now
    # acked_by stays NULL until auth lands (no authenticated principal yet);
    # audit row + acked_at capture the action.
    inc.updated_at = now

    session.add(IncidentEvent(
        incident_id=inc.id,
        type="ACK",
        from_state=prev_status,
        to_state="ACK",
        tier=inc.current_tier,
        payload={"idempotency_key": idempotency_key},
    ))
    return inc


async def resolve_incident(
    session: AsyncSession,
    incident_id: uuid.UUID,
    *,
    resolution_note: str = "",
    idempotency_key: str | None = None,
) -> Incident:
    """Set incident status → RESOLVED, clear next_fire_at, write RESOLVED audit row.

    Idempotent: if the incident is already RESOLVED returns the row unchanged.
    Raises HTTPException(404) if not found, HTTPException(409) if status invalid.
    The caller is responsible for committing the session.
    """
    inc = (
        await session.execute(select(Incident).where(Incident.id == incident_id))
    ).scalar_one_or_none()

    if inc is None:
        raise HTTPException(status_code=404, detail="Incident not found")

    if inc.status == IncidentStatus.RESOLVED:
        return inc

    if inc.status not in _RESOLVABLE_FROM:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot resolve incident in status {inc.status.value}",
        )

    now = datetime.now(tz=UTC)
    prev_status = inc.status.value
    inc.status = IncidentStatus.RESOLVED
    inc.next_fire_at = None
    inc.deadline_at = None
    inc.resolved_at = now
    # resolved_by stays NULL until auth lands (no authenticated principal yet);
    # audit row + resolved_at capture the action.
    inc.resolution_note = resolution_note or None
    inc.updated_at = now

    session.add(IncidentEvent(
        incident_id=inc.id,
        type="RESOLVED",
        from_state=prev_status,
        to_state="RESOLVED",
        tier=inc.current_tier,
        payload={
            "resolution_note": resolution_note,
            "idempotency_key": idempotency_key,
        },
    ))
    return inc
