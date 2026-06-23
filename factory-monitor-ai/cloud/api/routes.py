from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, Header, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from cloud.api.deps import get_session_maker
from cloud.common.db.models import Incident
from cloud.common.incident_actions import (
    acknowledge_incident as _ack_incident,
    resolve_incident as _resolve_incident,
)
from cloud.common.schemas.incident import IncidentListResponse, IncidentOut

router = APIRouter()


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
        inc = await _ack_incident(session, incident_id, idempotency_key=idempotency_key)
        await session.commit()

    return _ActionOut(incident_id=str(inc.id), status="ACK")


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
        inc = await _resolve_incident(
            session,
            incident_id,
            resolution_note=body.resolution_note,
            idempotency_key=idempotency_key,
        )
        await session.commit()

    return _ActionOut(incident_id=str(inc.id), status="RESOLVED")
