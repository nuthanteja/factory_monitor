"""Postgres-poll fallback for the live UI when Redis is down (design §8).

Selects incidents whose updated_at advanced past a watermark and broadcasts
each as a fresh incident.updated envelope via manager.broadcast(WsType, data),
then advances the watermark. This is the documented Redis-down degradation:
same data, just polled instead of pushed. Non-authoritative — it never writes,
only reads + broadcasts.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import async_sessionmaker

from cloud.common.db.models import Incident
from cloud.common.ws.contract import WsType
from cloud.common.ws.incident_view import build_incident_view

logger = logging.getLogger(__name__)

# Largest possible UUID — the initial keyset upper sentinel so a fresh start
# (since_id=MAX_UUID at time=now) replays no history.
MAX_UUID = uuid.UUID(int=(1 << 128) - 1)


async def poll_changes_once(
    session_maker: async_sessionmaker,
    manager: object,  # ConnectionManager (or any object with broadcast(WsType, dict)->int)
    *,
    since: datetime,
    since_id: uuid.UUID,
    batch: int,
) -> tuple[int, datetime, uuid.UUID]:
    """Broadcast incident.updated for rows after the compound (updated_at, id) cursor.

    Uses a row-value keyset  (updated_at, id) > (since, since_id)  ordered by
    (updated_at ASC, id ASC), so rows that share an exact updated_at microsecond
    are paged through deterministically and never skipped (the fix for the old
    single-column watermark). Returns (rows_broadcast, new_since, new_since_id).
    """
    async with session_maker() as s:
        rows = (
            await s.execute(
                select(Incident)
                .where(tuple_(Incident.updated_at, Incident.id) > tuple_(since, since_id))
                .order_by(Incident.updated_at.asc(), Incident.id.asc())
                .limit(batch)
            )
        ).scalars().all()

    new_since, new_since_id = since, since_id
    for inc in rows:
        data = build_incident_view(inc).model_dump()
        await manager.broadcast(WsType.INCIDENT_UPDATED, data)
        new_since, new_since_id = inc.updated_at, inc.id  # rows are ordered; last wins
    return len(rows), new_since, new_since_id


class PostgresPollFallback:
    """Runs poll_changes_once on a cadence, advancing its own watermark."""

    def __init__(
        self,
        session_maker: async_sessionmaker,
        manager: object,
        *,
        poll_seconds: float,
        batch: int,
    ) -> None:
        self._session_maker = session_maker
        self._manager = manager
        self._poll_seconds = poll_seconds
        self._batch = batch

    async def run(self, *, stop_event: asyncio.Event | None = None) -> None:
        """Poll Postgres on a cadence until stop_event is set or cancelled.

        Initialises the cursor to (now, MAX_UUID) so only changes that occur
        while the fallback is active are broadcast (no replay of prior history).
        """
        watermark = datetime.now(UTC)
        watermark_id = MAX_UUID
        logger.info(
            "ws postgres-poll fallback active interval=%.2fs batch=%d",
            self._poll_seconds,
            self._batch,
        )
        while stop_event is None or not stop_event.is_set():
            try:
                count, watermark, watermark_id = await poll_changes_once(
                    self._session_maker,
                    self._manager,
                    since=watermark,
                    since_id=watermark_id,
                    batch=self._batch,
                )
                if count:
                    logger.debug("ws fallback broadcast %d changed incidents", count)
            except Exception:  # noqa: BLE001 — keep the fallback alive on DB errors
                logger.exception("ws fallback poll error — continuing")
            try:
                await asyncio.sleep(self._poll_seconds)
            except asyncio.CancelledError:
                logger.info("ws postgres-poll fallback cancelled")
                raise
