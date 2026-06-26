from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from itertools import groupby

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from cloud.api.deps import get_session_maker
from cloud.common.db.models import Camera, Incident, Zone
from cloud.common.incident_actions import (
    acknowledge_incident as _ack_incident,
)
from cloud.common.incident_actions import (
    resolve_incident as _resolve_incident,
)
from cloud.common.schemas.camera import CameraListResponse, CameraOut
from cloud.common.schemas.heatmap import (
    CameraHeatmap,
    HeatmapCell,
    HeatmapResponse,
    ZoneListResponse,
    ZoneOut,
)
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


_WINDOW_MAX = timedelta(hours=6)
_WINDOW_DEFAULT = timedelta(minutes=15)


def _parse_window(s: str) -> timedelta:
    """Parse a window string like '15m' or '2h' into a timedelta.

    Accepted suffixes: m (minutes), h (hours).
    Default 15m if empty/None; capped at 6h; raises HTTPException(422) on bad input.
    """
    s = (s or "").strip()
    if not s:
        return _WINDOW_DEFAULT
    try:
        if s.endswith("m"):
            td = timedelta(minutes=int(s[:-1]))
        elif s.endswith("h"):
            td = timedelta(hours=int(s[:-1]))
        else:
            raise ValueError("unknown suffix")
        if td <= timedelta(0):
            raise ValueError("non-positive window")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid window '{s}': {exc}") from exc
    return min(td, _WINDOW_MAX)


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


@router.get("/api/v1/heatmap", response_model=HeatmapResponse)
async def get_heatmap(
    window: str = "15m",
    session_maker: async_sessionmaker = Depends(get_session_maker),
) -> HeatmapResponse:
    """Return the latest density snapshot per (camera_id, zone_id) within window."""
    td = _parse_window(window)
    server_now = datetime.now(tz=UTC)
    cutoff = server_now - td
    async with session_maker() as session:
        result = await session.execute(
            text(
                "SELECT DISTINCT ON (camera_id, zone_id)"
                " camera_id, zone_id, count, ts"
                " FROM density_snapshots"
                " WHERE ts >= :cutoff"
                " ORDER BY camera_id, zone_id, ts DESC"
            ),
            {"cutoff": cutoff},
        )
        rows = result.mappings().all()

    # Group rows per camera_id
    cameras: list[CameraHeatmap] = []
    sorted_rows = sorted(rows, key=lambda r: r["camera_id"] or "")
    for cam_id, group in groupby(sorted_rows, key=lambda r: r["camera_id"]):
        cells = [
            HeatmapCell(zone_id=r["zone_id"], count=r["count"], ts=r["ts"])
            for r in group
            if r["zone_id"] is not None
        ]
        cameras.append(CameraHeatmap(camera_id=cam_id or "", cells=cells))

    return HeatmapResponse(
        cameras=cameras,
        meta={"server_now": server_now.isoformat(), "window": window},
    )


@router.get("/api/v1/zones", response_model=ZoneListResponse)
async def list_zones(
    session_maker: async_sessionmaker = Depends(get_session_maker),
) -> ZoneListResponse:
    async with session_maker() as session:
        rows = (
            await session.execute(select(Zone).order_by(Zone.id))
        ).scalars().all()
    zones = [ZoneOut.model_validate(z) for z in rows]
    return ZoneListResponse(zones=zones)


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
