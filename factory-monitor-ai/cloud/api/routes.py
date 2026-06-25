from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Body, Depends, Header, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from cloud.api.deps import get_session_maker
from cloud.common.db.models import Camera, Incident
from cloud.common.incident_actions import (
    acknowledge_incident as _ack_incident,
)
from cloud.common.incident_actions import (
    resolve_incident as _resolve_incident,
)
from cloud.common.schemas.camera import CameraListResponse, CameraOut
from cloud.common.schemas.incident import IncidentListResponse, IncidentOut
from cloud.common.ws_events import (
    CHANGE_RESOLVED,
    CHANGE_UPDATED,
    incident_change,
)

router = APIRouter()


class _ActionOut(BaseModel):
    incident_id: str
    status: str


class _ResolveBody(BaseModel):
    resolution_note: str = ""


async def _publish_after(
    publisher: object,
    change_type: str,
    incident_id: uuid.UUID,
    **fields: object,
) -> None:
    """Best-effort publish of a compact change AFTER the txn committed.

    `publisher` is either a bound callable (tests inject a recorder or a
    redis-backed wrapper) or None.  None is a TRUE no-op — no live-Redis
    attempt is made.  Any callable failure is swallowed (best-effort).

    In production, the route handler passes ``request.app.state.ws_redis``
    (set by Task 25's lifespan).  In lightweight route tests the lifespan does
    not run, so ``app.state.ws_redis`` is absent → None → silent no-op.
    """
    if publisher is None:
        return
    change = incident_change(change_type, incident_id, **fields)
    if callable(publisher):
        try:
            await publisher(change)
        except Exception:  # noqa: BLE001 — best-effort even with an injected publisher
            pass


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/api/v1/cameras", response_model=CameraListResponse)
async def list_cameras(
    session_maker: async_sessionmaker = Depends(get_session_maker),
) -> CameraListResponse:
    async with session_maker() as session:
        rows = (
            await session.execute(select(Camera).order_by(Camera.id))
        ).scalars().all()
    cameras = [CameraOut.model_validate(row) for row in rows]
    return CameraListResponse(cameras=cameras)


@router.get("/api/v1/incidents", response_model=IncidentListResponse)
async def list_incidents(
    session_maker: async_sessionmaker = Depends(get_session_maker),
) -> IncidentListResponse:
    async with session_maker() as session:
        rows = (
            await session.execute(select(Incident).order_by(Incident.created_at.desc()))
        ).scalars().all()
    incidents = [IncidentOut.model_validate(row) for row in rows]
    server_now = datetime.now(tz=UTC).isoformat()
    return IncidentListResponse(incidents=incidents, meta={"server_now": server_now})


@router.post(
    "/api/v1/incidents/{incident_id}/acknowledge",
    response_model=_ActionOut,
    status_code=status.HTTP_200_OK,
)
async def acknowledge_incident(
    request: Request,
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

    redis = getattr(request.app.state, "ws_redis", None)
    await _publish_after(redis, CHANGE_UPDATED, inc.id, status="ACK")
    return _ActionOut(incident_id=str(inc.id), status="ACK")


@router.post(
    "/api/v1/incidents/{incident_id}/resolve",
    response_model=_ActionOut,
    status_code=status.HTTP_200_OK,
)
async def resolve_incident(
    request: Request,
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

    redis = getattr(request.app.state, "ws_redis", None)
    await _publish_after(redis, CHANGE_RESOLVED, inc.id, resolved_at=inc.resolved_at)
    return _ActionOut(incident_id=str(inc.id), status="RESOLVED")
