"""Notifier relay: drains the outbox table and delivers via NotificationProvider.

Hot loop (run_once):
  SELECT … FROM outbox WHERE status='PENDING' AND next_attempt_at<=now()
  FOR UPDATE SKIP LOCKED LIMIT :batch

For each row:
  1. Call provider_chain.send(idempotency_key=str(row.id)).
  2. On 'sent'  → status='SENT', sent_at=now(), provider_sid; INSERT messages(direction='out').
  3. On non-sent (degraded/failed) with attempts < max_attempts-1
               → attempts++, next_attempt_at=now()+backoff.
  4. On non-sent with attempts >= max_attempts-1
               → status='DEAD' (alert logged).

All mutations commit per-row so a crash mid-batch leaves earlier rows SENT and
later rows PENDING (they re-queue on the next poll — safe at-least-once delivery).

Recovery is free: a crashed relay leaves rows PENDING; the next run_once picks
them up because next_attempt_at is past.

Delivery NEVER blocks the escalation state machine: tiers advance on next_fire_at
regardless of outbox status.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from cloud.common.db.models import Message, Outbox
from cloud.notifications.chain import ProviderChain
from cloud.notifications.provider import NotificationKind

logger = logging.getLogger("factory_monitor.notifier_worker.relay")

_BATCH = 50
_BACKOFF_BASE_SECONDS = 10  # backoff = base * 2^(attempts-1), capped at 1h


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
) -> int:
    """Drain one batch of due PENDING outbox rows.  Returns count of rows touched."""
    processed = 0

    async with session_maker() as session:
        # Claim a batch with FOR UPDATE SKIP LOCKED — safe for N relay replicas.
        rows_result = await session.execute(
            select(Outbox)
            .where(Outbox.status == "PENDING")
            .where(Outbox.next_attempt_at <= text("now()"))
            .order_by(Outbox.next_attempt_at)
            .limit(batch)
            .with_for_update(skip_locked=True)
        )
        rows: list[Outbox] = list(rows_result.scalars().all())

    # Process each row in its own transaction so failures don't roll back earlier successes.
    for row in rows:
        await _process_row(session_maker, provider_chain, row, backoff_base=backoff_base)
        processed += 1

    return processed


async def _process_row(
    session_maker: async_sessionmaker,
    provider_chain: ProviderChain,
    row: Outbox,
    *,
    backoff_base: int,
) -> None:
    kind = NotificationKind(row.kind)

    result = await provider_chain.send(
        row.to_phone_e164,
        kind,
        template_name=row.template_name,
        variables=dict(row.variables) if row.variables else None,
        body=row.body,
        idempotency_key=str(row.id),
    )

    now = datetime.now(tz=timezone.utc)

    async with session_maker() as session:
        # Re-fetch WITH a row lock so a concurrent replica that also fetched this row
        # in the poll batch cannot commit a duplicate send+message insert.  If another
        # worker already claimed and processed the row, ob will be non-PENDING and we
        # bail out immediately — delivering the idempotency guarantee.
        ob = (
            await session.execute(
                select(Outbox).where(Outbox.id == row.id).with_for_update()
            )
        ).scalar_one_or_none()
        if ob is None or ob.status != "PENDING":
            return  # another worker already claimed/processed this row

        if result.status == "sent":
            ob.status = "SENT"
            ob.sent_at = now
            ob.provider_sid = result.sid
            ob.attempts = ob.attempts + 1

            # Insert the outbound messages row — the Twilio webhook matches inbound
            # replies by querying Message(direction='out', status='sent',
            # to_phone_e164=<sender>).  These three fields MUST be populated for the
            # webhook ACK/RESOLVE match to work (Task-2 contract).
            msg = Message(
                id=uuid.uuid4(),
                incident_id=ob.incident_id,
                direction="out",
                channel=result.channel,    # actual delivery channel (not the intent)
                to_phone_e164=ob.to_phone_e164,
                provider_sid=result.sid,
                status="sent",
            )
            session.add(msg)
            logger.info(
                "outbox id=%s SENT via %s sid=%s idem_key=%s",
                row.id,
                result.channel,
                result.sid,
                str(row.id),
            )
        else:
            new_attempts = ob.attempts + 1
            ob.attempts = new_attempts

            if new_attempts >= ob.max_attempts:
                ob.status = "DEAD"
                logger.error(
                    "outbox id=%s DEAD after %d attempts — ALERT: delivery failed permanently",
                    row.id,
                    new_attempts,
                )
            else:
                ob.next_attempt_at = now + _backoff(new_attempts, backoff_base)
                logger.warning(
                    "outbox id=%s delivery %s (attempt %d/%d); retry at %s",
                    row.id,
                    result.status,
                    new_attempts,
                    ob.max_attempts,
                    ob.next_attempt_at.isoformat(),
                )

        await session.commit()


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
