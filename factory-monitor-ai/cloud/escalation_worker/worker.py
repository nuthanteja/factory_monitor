"""Escalation worker poll loop — §6 design spec.

poll_once(session_maker, worker_id, lease_seconds, batch) -> int
  Claim all due incidents with SELECT … FOR UPDATE SKIP LOCKED, call
  fire_transition() in one txn per incident, release the claim.

EscalationWorker
  Thin wrapper that loops poll_once every poll_interval_seconds with a
  graceful shutdown on stop().
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cloud.common.db.models import Incident, IncidentStatus
from cloud.escalation_worker.transition import TransitionResult, fire_transition

logger = logging.getLogger(__name__)

_ACTIVE_STATUSES = (
    IncidentStatus.AWAITING_OPERATOR.value,
    IncidentStatus.TIER1.value,
    IncidentStatus.TIER2.value,
)

# SQL that claims a batch of due incidents atomically.
# claimed_by/claimed_until lease guards against long-running workers that stall
# after claiming but before committing the transition (crash recovery path).
_CLAIM_SQL = text(
    """
    UPDATE incidents i
    SET claimed_by = :worker_id,
        claimed_until = now() + make_interval(secs => :lease_seconds)
    WHERE i.id IN (
        SELECT id FROM incidents
        WHERE status = ANY(:statuses)
          AND next_fire_at <= now()
          AND (claimed_until IS NULL OR claimed_until < now())
        ORDER BY next_fire_at
        FOR UPDATE SKIP LOCKED
        LIMIT :batch
    )
    RETURNING i.id, i.site_id, i.camera_id, i.zone_id, i.anomaly_type,
              i.rule_id, i.object_class, i.track_id, i.severity,
              i.dedup_key, i.status, i.current_tier, i.next_fire_at,
              i.deadline_at, i.claimed_by, i.claimed_until, i.snapshot_url,
              i.acked_by, i.acked_at, i.resolved_by, i.resolved_at,
              i.resolution_note, i.is_synthetic, i.created_at, i.updated_at
    """
)

_RELEASE_CLAIM_SQL = text(
    "UPDATE incidents SET claimed_by = NULL, claimed_until = NULL WHERE id = :id"
)


async def poll_once(
    session_maker: async_sessionmaker[AsyncSession],
    *,
    worker_id: str,
    lease_seconds: int = 30,
    batch: int = 10,
) -> int:
    """Claim + transition one batch of due incidents.  Returns rows processed."""
    processed = 0

    async with session_maker() as claim_session:
        rows = (
            await claim_session.execute(
                _CLAIM_SQL,
                {
                    "worker_id": worker_id,
                    "lease_seconds": lease_seconds,
                    "statuses": list(_ACTIVE_STATUSES),
                    "batch": batch,
                },
            )
        ).fetchall()
        await claim_session.commit()

    for row in rows:
        incident_id = row[0]
        try:
            async with session_maker() as txn_session:
                # Re-fetch inside the transition txn so SQLAlchemy ORM state is fresh
                incident = await txn_session.get(Incident, incident_id)
                if incident is None:
                    continue
                result: TransitionResult = await fire_transition(txn_session, incident)
                await txn_session.commit()
                if result.fired:
                    processed += 1
                    logger.info(
                        "escalation fired incident_id=%s new_status=%s",
                        incident_id, result.new_status,
                    )
                elif result.skipped_idempotent:
                    logger.debug(
                        "escalation skipped (idempotent) incident_id=%s", incident_id
                    )
        except Exception:
            logger.exception("error processing incident_id=%s — claim will expire naturally", incident_id)
            # Release claim eagerly so it's re-claimable without waiting for lease expiry
            try:
                async with session_maker() as rel_session:
                    await rel_session.execute(_RELEASE_CLAIM_SQL, {"id": incident_id})
                    await rel_session.commit()
            except Exception:
                logger.warning("failed to release claim for incident_id=%s", incident_id)

    return processed


class EscalationWorker:
    """Thin wrapper that runs poll_once in a loop until stop() is called."""

    def __init__(
        self,
        session_maker: async_sessionmaker[AsyncSession],
        *,
        worker_id: str | None = None,
        poll_interval_seconds: float = 1.0,
        lease_seconds: int = 30,
        batch: int = 10,
    ) -> None:
        self._session_maker = session_maker
        self._worker_id = worker_id or f"worker-{uuid.uuid4().hex[:8]}"
        self._poll_interval = poll_interval_seconds
        self._lease_seconds = lease_seconds
        self._batch = batch
        self._running = False

    async def start(self) -> None:
        self._running = True
        logger.info("escalation worker %s starting", self._worker_id)

    async def stop(self) -> None:
        logger.info("escalation worker %s stopping", self._worker_id)
        self._running = False

    async def run_until_stopped(self) -> None:
        """Poll loop — runs until stop() sets _running=False."""
        while self._running:
            try:
                processed = await poll_once(
                    self._session_maker,
                    worker_id=self._worker_id,
                    lease_seconds=self._lease_seconds,
                    batch=self._batch,
                )
                if processed:
                    logger.debug("worker %s processed %d incidents", self._worker_id, processed)
            except Exception:
                logger.exception("worker %s poll error — continuing", self._worker_id)
            await asyncio.sleep(self._poll_interval)
        logger.info("escalation worker %s stopped", self._worker_id)
