"""Notifier relay: drains the outbox table and delivers via NotificationProvider.

Hot loop (run_once) — two-phase SENDING claim:
  Phase 1 (one committed txn): atomically flip due PENDING rows (and stale SENDING rows
    whose lease expired) to SENDING, stamping a claimed_by/claimed_until lease.
  Phase 2/3 (per-row, own txn): provider.send() then settle SENDING→SENT|PENDING|DEAD.

For each claimed row:
  1. Call provider_chain.send(idempotency_key=str(row.id)).
  2. On 'sent'  → status='SENT', sent_at=now(), provider_sid; INSERT messages(direction='out').
  3. On non-sent with attempts < max_attempts → status='PENDING', next_attempt_at=now()+backoff.
  4. On non-sent with attempts >= max_attempts → status='DEAD' (alert logged).

Crash recovery: a crashed relay leaves rows in SENDING; the next run_once reclaims them
after the lease expires (the same claim query selects stale SENDING rows). The provider's
idempotency_key (str(row.id)) collapses re-sends into one effect at the receiver.

Delivery NEVER blocks the escalation state machine: tiers advance on next_fire_at
regardless of outbox status.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from opentelemetry import trace as _otel_trace
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from cloud.common.db.models import Message, Outbox
from cloud.notifications.chain import ProviderChain
from cloud.notifications.provider import NotificationKind

logger = logging.getLogger("factory_monitor.notifier_worker.relay")
_tracer = _otel_trace.get_tracer("factory_monitor.notifier_worker")

_BATCH = 50
_BACKOFF_BASE_SECONDS = 10  # backoff = base * 2^(attempts-1), capped at 1h
_DEFAULT_LEASE_SECONDS = 30

# Two-phase claim: atomically flip due PENDING rows (and stale SENDING rows whose
# lease expired — the reaper) to SENDING, stamping a lease. SKIP LOCKED makes this
# safe for N relay replicas; the committed SENDING state means a crash mid-send
# leaves a recoverable in-flight row, never an ambiguous PENDING re-send.
_CLAIM_SQL = text(
    """
    UPDATE outbox o
    SET status = 'SENDING',
        claimed_by = :worker_id,
        claimed_until = now() + make_interval(secs => :lease_seconds),
        attempts = o.attempts + 1
    WHERE o.id IN (
        SELECT id FROM outbox
        WHERE (status = 'PENDING' AND next_attempt_at <= now())
           OR (status = 'SENDING' AND claimed_until < now())
        ORDER BY next_attempt_at
        FOR UPDATE SKIP LOCKED
        LIMIT :batch
    )
    RETURNING o.id, o.incident_id, o.tier, o.to_phone_e164, o.channel, o.kind,
              o.template_name, o.variables, o.body, o.idempotency_key,
              o.attempts, o.max_attempts
    """
)


def _backoff(attempts: int, base: int = _BACKOFF_BASE_SECONDS) -> timedelta:
    """Exponential backoff capped at 1 hour."""
    delay = base * (2 ** max(attempts - 1, 0))
    return timedelta(seconds=min(delay, 3600))


async def run_once(
    session_maker: async_sessionmaker,
    provider_chain: ProviderChain,
    *,
    batch: int = _BATCH,
    backoff_base: int = _BACKOFF_BASE_SECONDS,
    worker_id: str | None = None,
    lease_seconds: int = _DEFAULT_LEASE_SECONDS,
    fault_hook: Callable[[uuid.UUID], Awaitable[None]] | None = None,
) -> int:
    """Claim a batch into SENDING, then send+settle each. Returns rows claimed.

    Phase 1 (one committed txn): atomically claim due PENDING + stale SENDING rows.
    Phase 2/3 (per-row, own txn): provider.send() then settle SENDING→SENT|PENDING|DEAD.
    fault_hook (tests only) is awaited between send and settle to simulate a crash.
    """
    wid = worker_id or f"notifier-{uuid.uuid4().hex[:8]}"

    async with session_maker() as session:
        claimed = (
            await session.execute(
                _CLAIM_SQL,
                {"worker_id": wid, "lease_seconds": lease_seconds, "batch": batch},
            )
        ).fetchall()
        await session.commit()

    for row in claimed:
        await _settle_row(
            session_maker, provider_chain, row, backoff_base=backoff_base, fault_hook=fault_hook
        )

    return len(claimed)


async def _settle_row(
    session_maker: async_sessionmaker,
    provider_chain: ProviderChain,
    row: object,  # a claim RETURNING row (row.id, row.kind, row.to_phone_e164, ...)
    *,
    backoff_base: int,
    fault_hook: Callable[[uuid.UUID], Awaitable[None]] | None = None,
) -> None:
    kind = NotificationKind(row.kind)

    with _tracer.start_as_current_span(
        "notifier.send",
        attributes={
            "outbox_id": str(row.id),
            **({"incident_id": str(row.incident_id)} if row.incident_id else {}),
        },
    ):
        result = await provider_chain.send(
            row.to_phone_e164,
            kind,
            template_name=row.template_name,
            variables=dict(row.variables) if row.variables else None,
            body=row.body,
            idempotency_key=str(row.id),
        )

    # Crash seam: the row is committed-SENDING and the message may already be out.
    # If we die here, the row is reclaimed after its lease and re-sent — the
    # idempotent receiver collapses the re-send into one effect (Task 3 proves it).
    if fault_hook is not None:
        await fault_hook(row.id)

    now = datetime.now(tz=UTC)

    async with session_maker() as session:
        ob = (
            await session.execute(
                select(Outbox).where(Outbox.id == row.id).with_for_update()
            )
        ).scalar_one_or_none()
        # Only the worker still holding the SENDING claim settles it. If another
        # worker reclaimed it (our lease expired) it is no longer ours — bail.
        if ob is None or ob.status != "SENDING":
            return

        if result.status == "sent":
            ob.status = "SENT"
            ob.sent_at = now
            ob.provider_sid = result.sid
            ob.claimed_by = None
            ob.claimed_until = None
            session.add(
                Message(
                    id=uuid.uuid4(),
                    incident_id=ob.incident_id,
                    direction="out",
                    channel=result.channel,
                    to_phone_e164=ob.to_phone_e164,
                    provider_sid=result.sid,
                    status="sent",
                )
            )
            logger.info(
                "outbox id=%s SENT via %s sid=%s idem_key=%s",
                row.id, result.channel, result.sid, str(row.id),
            )
        elif ob.attempts >= ob.max_attempts:
            ob.status = "DEAD"
            ob.claimed_by = None
            ob.claimed_until = None
            logger.error(
                "outbox id=%s DEAD after %d attempts — ALERT: delivery failed permanently",
                row.id, ob.attempts,
            )
        else:
            ob.status = "PENDING"
            ob.next_attempt_at = now + _backoff(ob.attempts, backoff_base)
            ob.claimed_by = None
            ob.claimed_until = None
            logger.warning(
                "outbox id=%s delivery %s (attempt %d/%d); retry at %s",
                row.id, result.status, ob.attempts, ob.max_attempts,
                ob.next_attempt_at.isoformat(),
            )

        await session.commit()


class NotifierRelay:
    """Class wrapper around run_once() for use by the test suite.

    Provides the .drain_once() method declared in the Task-18 interface contract.
    Accepts either a ProviderChain or a bare list of providers (auto-wrapped).
    """

    def __init__(
        self,
        session_maker: async_sessionmaker,
        provider_chain: ProviderChain | list,
        *,
        batch: int = _BATCH,
        backoff_base: int = _BACKOFF_BASE_SECONDS,
    ) -> None:
        self._session_maker = session_maker
        if isinstance(provider_chain, ProviderChain):
            self._chain = provider_chain
        else:
            # Bare list of providers: wrap in ProviderChain
            self._chain = ProviderChain(provider_chain)
        self._batch = batch
        self._backoff_base = backoff_base

    async def drain_once(self) -> int:
        """Drain all PENDING outbox rows that are due now.

        Returns number of outbox rows processed this pass (includes SENT, failed, and DEAD).
        """
        return await run_once(
            self._session_maker,
            self._chain,
            batch=self._batch,
            backoff_base=self._backoff_base,
        )


async def run_forever(
    session_maker: async_sessionmaker,
    provider_chain: ProviderChain,
    *,
    poll_interval: float = 1.0,
) -> None:
    """Poll the outbox continuously.  Cancelled by asyncio.CancelledError."""
    logger.info("notifier relay started poll_interval=%.1fs", poll_interval)
    while True:
        try:
            count = await run_once(session_maker, provider_chain)
            if count:
                logger.debug("relay processed %d outbox row(s)", count)
        except Exception:
            logger.exception("relay poll error — will retry after interval")
        await asyncio.sleep(poll_interval)
