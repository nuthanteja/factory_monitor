from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, Header, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from cloud.api.deps import get_session_maker
from cloud.common.db.models import Incident, IncidentEvent, IncidentStatus
from cloud.common.schemas.incident import IncidentListResponse, IncidentOut

router = APIRouter()

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


class _ActionOut(BaseModel):
    incident_id: str
    status: str


class _ResolveBody(BaseModel):
    resolution_note: str = ""


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/api/v1/incidents", response_model=IncidentListResponse)
async def list_incidents(
    session_maker: async_sessionmaker = Depends(get_session_maker),
) -> IncidentListResponse:
    async with session_maker() as session:
        rows = (
            await session.execute(select(Incident).order_by(Incident.created_at.desc()))
        ).scalars().all()
    incidents = [IncidentOut.model_validate(row) for row in rows]
    server_now = datetime.now(tz=timezone.utc).isoformat()
    return IncidentListResponse(incidents=incidents, meta={"server_now": server_now})


@router.post(
    "/api/v1/incidents/{incident_id}/acknowledge",
    response_model=_ActionOut,
    status_code=status.HTTP_200_OK,
)
async def acknowledge_incident(
    incident_id: uuid.UUID,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session_maker: async_sessionmaker = Depends(get_session_maker),
) -> _ActionOut:
    """Set incident status → ACK, clear next_fire_at, write ACK audit row.

    Idempotent: if the incident is already ACK and the same Idempotency-Key is
    re-sent, returns 200 without creating a duplicate audit row.
    """
    async with session_maker() as session:
        inc = (
            await session.execute(select(Incident).where(Incident.id == incident_id))
        ).scalar_one_or_none()

        if inc is None:
            raise HTTPException(status_code=404, detail="Incident not found")

        if inc.status == IncidentStatus.ACK:
            # Already acknowledged — idempotent return
            return _ActionOut(incident_id=str(inc.id), status="ACK")

        if inc.status not in _ACKABLE_FROM:
            raise HTTPException(
                status_code=409,
                detail=f"Cannot acknowledge incident in status {inc.status.value}",
            )

        now = datetime.now(tz=timezone.utc)
        prev_status = inc.status.value
        inc.status = IncidentStatus.ACK
        inc.next_fire_at = None
        inc.deadline_at = None
        inc.acked_at = now
        # acked_by stays NULL until auth lands (no authenticated principal yet); audit row + acked_at capture the action.
        inc.updated_at = now

        session.add(IncidentEvent(
            incident_id=inc.id,
            type="ACK",
            from_state=prev_status,
            to_state="ACK",
            tier=inc.current_tier,
            payload={"idempotency_key": idempotency_key},
        ))
        await session.commit()

    return _ActionOut(incident_id=str(incident_id), status="ACK")


@router.post(
    "/api/v1/incidents/{incident_id}/resolve",
    response_model=_ActionOut,
    status_code=status.HTTP_200_OK,
)
async def resolve_incident(
    incident_id: uuid.UUID,
    body: _ResolveBody = Body(default_factory=_ResolveBody),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session_maker: async_sessionmaker = Depends(get_session_maker),
) -> _ActionOut:
    """Set incident status → RESOLVED, clear next_fire_at, write RESOLVED audit row."""
    async with session_maker() as session:
        inc = (
            await session.execute(select(Incident).where(Incident.id == incident_id))
        ).scalar_one_or_none()

        if inc is None:
            raise HTTPException(status_code=404, detail="Incident not found")

        if inc.status == IncidentStatus.RESOLVED:
            return _ActionOut(incident_id=str(inc.id), status="RESOLVED")

        if inc.status not in _RESOLVABLE_FROM:
            raise HTTPException(
                status_code=409,
                detail=f"Cannot resolve incident in status {inc.status.value}",
            )

        now = datetime.now(tz=timezone.utc)
        prev_status = inc.status.value
        inc.status = IncidentStatus.RESOLVED
        inc.next_fire_at = None
        inc.deadline_at = None
        inc.resolved_at = now
        # resolved_by stays NULL until auth lands (no authenticated principal yet); audit row + resolved_at capture the action.
        inc.resolution_note = body.resolution_note or None
        inc.updated_at = now

        session.add(IncidentEvent(
            incident_id=inc.id,
            type="RESOLVED",
            from_state=prev_status,
            to_state="RESOLVED",
            tier=inc.current_tier,
            payload={
                "resolution_note": body.resolution_note,
                "idempotency_key": idempotency_key,
            },
        ))
        await session.commit()

    return _ActionOut(incident_id=str(incident_id), status="RESOLVED")
