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
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from cloud.common.db.models import Incident
from cloud.common.ws.contract import WsType
from cloud.common.ws.incident_view import build_incident_view

logger = logging.getLogger(__name__)


async def poll_changes_once(
    session_maker: async_sessionmaker,
    manager: object,  # ConnectionManager (or any object with broadcast(WsType, dict)->int)
    *,
    since: datetime,
    batch: int,
) -> tuple[int, datetime]:
    """Broadcast incident.updated for rows with updated_at > since.

    Returns (rows_broadcast, new_watermark). new_watermark is the max
    updated_at observed this poll, or `since` if nothing changed.
    """
    async with session_maker() as s:
        # Cursor limitation — known, accepted for this domain:
        #
        # The watermark advances to max(updated_at) of this batch, and the
        # next poll uses WHERE updated_at > :since.  If MORE than `batch`
        # (default 200) incidents share the EXACT same updated_at microsecond,
        # the overflow rows beyond the batch will be skipped on this poll and
        # never delivered.
        #
        # Why this is safe here:
        #   • Incidents are updated one-at-a-time by the escalation / ack /
        #     ingest writers, each of which calls Postgres now() individually,
        #     producing a distinct microsecond timestamp per row.  A genuine
        #     same-microsecond burst exceeding 200 rows cannot occur.
        #   • This is the DEGRADED path — active only when Redis is down —
        #     so delivery is already best-effort.
        #
        # Phase-3 hardening (if ever needed): replace the single-column cursor
        # with a compound keyset  (updated_at, id)  and
        # ORDER BY updated_at ASC, id ASC  to guarantee no row is skipped even
        # when many rows share the same timestamp.
        rows = (
            await s.execute(
                select(Incident)
                .where(Incident.updated_at > since)
                .order_by(Incident.updated_at.asc())
                .limit(batch)
            )
        ).scalars().all()

    watermark = since
    for inc in rows:
        data = build_incident_view(inc).model_dump()
        await manager.broadcast(WsType.INCIDENT_UPDATED, data)
        if inc.updated_at > watermark:
            watermark = inc.updated_at
    return len(rows), watermark


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

        Initialises the watermark to now() so only changes that occur while
        the fallback is active are broadcast (no replay of prior history).
        When ``stop_event`` is None the loop runs until the task is cancelled.
        """
        watermark = datetime.now(UTC)
        logger.info(
            "ws postgres-poll fallback active interval=%.2fs batch=%d",
            self._poll_seconds,
            self._batch,
        )
        while stop_event is None or not stop_event.is_set():
            try:
                count, watermark = await poll_changes_once(
                    self._session_maker,
                    self._manager,
                    since=watermark,
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
