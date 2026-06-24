"""/ws/live WebSocket endpoint (design §5.5).

On connect: accept, query active incidents, send a `snapshot`. Then run a
receive loop (handles the client subscribe message) and a periodic broadcaster
(system.heartbeat clock re-anchor + timer.snapshot deadline re-anchor)
concurrently until the socket closes. The browser is render-only: it never
computes escalation, it just re-anchors on server_now + deadline_at.

This slice does NOT push incident.created/updated/etc — those come from the
Redis fan-out (slice ws-redis) calling app.state.ws_manager.broadcast(...).
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from cloud.common.db.models import Incident, IncidentStatus
from cloud.common.ws.contract import WsType
from cloud.common.ws.incident_view import build_incident_view
from cloud.common.ws.manager import Connection, ConnectionManager

ws_router = APIRouter()

WS_HEARTBEAT_SECONDS = 10.0
WS_TIMER_SNAPSHOT_SECONDS = 15.0

_ACTIVE_STATUSES = (
    IncidentStatus.AWAITING_OPERATOR,
    IncidentStatus.TIER1,
    IncidentStatus.TIER2,
    IncidentStatus.CRITICAL_UNRESOLVED,
)


async def get_active_incident_views(session_maker: async_sessionmaker) -> list:
    async with session_maker() as session:
        rows = (
            await session.execute(
                select(Incident)
                .where(Incident.status.in_(_ACTIVE_STATUSES))
                .order_by(Incident.created_at.desc())
            )
        ).scalars().all()
    return [build_incident_view(r) for r in rows]


async def _timer_rows(session_maker: async_sessionmaker) -> list[dict]:
    views = await get_active_incident_views(session_maker)
    out = []
    for v in views:
        dumped = v.model_dump(mode="json")
        out.append(
            {
                "incident_id": dumped["incident_id"],
                "deadline_at": dumped["deadline_at"],
                "current_tier": dumped["current_tier"],
            }
        )
    return out


async def _receive_loop(ws: WebSocket, mgr: ConnectionManager, conn: Connection) -> None:
    """Handle inbound client messages (currently: subscribe). On a subscribe,
    re-anchor the client immediately with a heartbeat (carries fresh server_now)."""
    while True:
        msg = await ws.receive_json()
        if isinstance(msg, dict) and msg.get("action") == "subscribe":
            topics = msg.get("topics") or []
            last_seq = int(msg.get("last_seq") or 0)
            mgr.subscribe(conn, topics, last_seq)
            await mgr.send(conn, WsType.SYSTEM_HEARTBEAT, {})


async def _broadcast_loop(
    mgr: ConnectionManager,
    conn: Connection,
    session_maker: async_sessionmaker,
    heartbeat_s: float,
    timer_s: float,
) -> None:
    """Per-connection periodic re-anchor: heartbeat + timer.snapshot.

    Independent timers so the two cadences don't couple; both use the manager's
    per-connection seq via send()."""
    next_hb = heartbeat_s
    next_timer = timer_s
    elapsed = 0.0
    tick = min(heartbeat_s, timer_s)
    while True:
        await asyncio.sleep(tick)
        elapsed += tick
        if elapsed + 1e-9 >= next_hb:
            await mgr.send(conn, WsType.SYSTEM_HEARTBEAT, {})
            next_hb += heartbeat_s
        if elapsed + 1e-9 >= next_timer:
            rows = await _timer_rows(session_maker)
            await mgr.send(conn, WsType.TIMER_SNAPSHOT, {"incidents": rows})
            next_timer += timer_s


@ws_router.websocket("/ws/live")
async def ws_live(ws: WebSocket) -> None:
    mgr: ConnectionManager = ws.app.state.ws_manager
    session_maker: async_sessionmaker = ws.app.state.ws_session_maker
    heartbeat_s = float(getattr(ws.app.state, "ws_heartbeat_seconds", WS_HEARTBEAT_SECONDS))
    timer_s = float(getattr(ws.app.state, "ws_timer_snapshot_seconds", WS_TIMER_SNAPSHOT_SECONDS))

    conn = await mgr.connect(ws)
    try:
        views = await get_active_incident_views(session_maker)
        await mgr.send(
            conn,
            WsType.SNAPSHOT,
            {"incidents": [v.model_dump(mode="json") for v in views]},
        )
        recv = asyncio.create_task(_receive_loop(ws, mgr, conn))
        beat = asyncio.create_task(
            _broadcast_loop(mgr, conn, session_maker, heartbeat_s, timer_s)
        )
        done, pending = await asyncio.wait(
            {recv, beat}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in done:
            exc = task.exception()
            if exc is not None and not isinstance(exc, WebSocketDisconnect):
                raise exc
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
    except WebSocketDisconnect:
        pass
    finally:
        mgr.disconnect(conn)
