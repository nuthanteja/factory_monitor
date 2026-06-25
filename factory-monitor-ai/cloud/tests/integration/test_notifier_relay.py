"""Integration tests: Notifier relay against a real testcontainers Postgres.

Test matrix:
  1. PENDING row → ConsoleProvider → row becomes SENT, messages(direction='out') row inserted.
  2. Failing provider: attempts++ + next_attempt_at advances; row stays PENDING until max_attempts.
  3. At max_attempts: row becomes DEAD, no further attempts.
  4. Two PENDING rows for different incidents: both delivered, two messages rows.
  5. Row with next_attempt_at in the future is skipped.
  6. Concurrent relay replicas → two-phase claim means only one wins; one send, one messages row.
  7. SENDING row with expired lease is reclaimed and sent (crash-recovery path).
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from cloud.common.db.models import Message, Outbox
from cloud.notifications.chain import ProviderChain
from cloud.notifications.console import ConsoleProvider
from cloud.notifications.provider import ProviderResult
from cloud.notifier_worker.relay import run_once

MIGRATIONS = str(Path(__file__).resolve().parents[3] / "cloud" / "migrations")


def _async_url(sync_url: str) -> str:
    return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://").replace(
        "postgresql://", "postgresql+asyncpg://"
    )


@pytest.fixture(scope="module")
def pg():
    with PostgresContainer("postgres:16") as container:
        sync_url = container.get_connection_url()
        cfg = Config()
        cfg.set_main_option("script_location", MIGRATIONS)
        cfg.set_main_option("sqlalchemy.url", sync_url)
        command.upgrade(cfg, "head")
        yield _async_url(sync_url)


@pytest.fixture
async def maker(pg: str):
    engine = create_async_engine(pg, future=True)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    yield sm
    await engine.dispose()


async def _seed_incident(session: AsyncSession) -> uuid.UUID:
    """Insert a minimal incident and return its id."""
    inc_id = uuid.uuid4()
    now = datetime.now(tz=UTC)
    await session.execute(
        text(
            """
            INSERT INTO incidents
              (id, site_id, camera_id, anomaly_type, rule_id, severity, dedup_key,
               status, current_tier, next_fire_at, is_synthetic, created_at, updated_at)
            VALUES
              (:id, 'plant-01', 'cam_01', 'ppe_no_hardhat', 'PPE_NO_HARDHAT', 'high',
               :dk, 'AWAITING_OPERATOR', 0, :nfa, false, now(), now())
            """
        ),
        {"id": str(inc_id), "dk": f"dk-{inc_id}", "nfa": now + timedelta(seconds=120)},
    )
    return inc_id


async def _seed_outbox(
    session: AsyncSession,
    incident_id: uuid.UUID,
    *,
    idem_key: str | None = None,
    next_attempt_at: datetime | None = None,
    max_attempts: int = 6,
    attempts: int = 0,
) -> uuid.UUID:
    ob_id = uuid.uuid4()
    na = next_attempt_at or datetime.now(tz=UTC)
    await session.execute(
        text(
            """
            INSERT INTO outbox
              (id, incident_id, tier, to_phone_e164, channel, kind,
               template_name, variables, idempotency_key, status,
               attempts, max_attempts, next_attempt_at, created_at)
            VALUES
              (:id, :inc, 0, '+10000000001', 'whatsapp', 'TEMPLATE',
               'alert_operator', '{"zone":"weld_bay"}', :idem, 'PENDING',
               :att, :max, :na, now())
            """
        ),
        {
            "id": str(ob_id),
            "inc": str(incident_id),
            "idem": idem_key or str(ob_id),
            "att": attempts,
            "max": max_attempts,
            "na": na,
        },
    )
    return ob_id


# ── Test 1: PENDING → ConsoleProvider → SENT + messages row ───────────────────

@pytest.mark.asyncio
@pytest.mark.integration
async def test_pending_row_delivered_and_marked_sent(maker: async_sessionmaker):
    async with maker() as s:
        inc_id = await _seed_incident(s)
        ob_id = await _seed_outbox(s, inc_id)
        await s.commit()

    chain = ProviderChain([ConsoleProvider()])
    processed = await run_once(maker, chain)
    assert processed >= 1

    async with maker() as s:
        ob = (await s.execute(select(Outbox).where(Outbox.id == ob_id))).scalar_one()
        assert ob.status == "SENT"
        assert ob.sent_at is not None
        assert ob.provider_sid is None  # console returns sid=None

        msgs = (
            await s.execute(select(Message).where(Message.incident_id == inc_id))
        ).scalars().all()
        assert len(msgs) == 1
        assert msgs[0].direction == "out"
        assert msgs[0].channel == "console"  # ConsoleProvider returns channel="console"


# ── Test 2: Failing provider retries with incremented attempts ─────────────────

@pytest.mark.asyncio
@pytest.mark.integration
async def test_failing_provider_increments_attempts_and_stays_pending(
    maker: async_sessionmaker,
):
    async with maker() as s:
        inc_id = await _seed_incident(s)
        ob_id = await _seed_outbox(s, inc_id, max_attempts=3)
        await s.commit()

    # Stub provider that always fails
    failing = AsyncMock()
    failing.send = AsyncMock(
        return_value=ProviderResult(sid=None, status="failed", channel="whatsapp")
    )
    chain = ProviderChain([failing])

    await run_once(maker, chain)

    async with maker() as s:
        ob = (await s.execute(select(Outbox).where(Outbox.id == ob_id))).scalar_one()
    assert ob.status == "PENDING"
    assert ob.attempts == 1
    # next_attempt_at must be in the future (backoff applied)
    now = datetime.now(tz=UTC)
    naa = ob.next_attempt_at
    if naa.tzinfo is None:
        naa = naa.replace(tzinfo=UTC)
    assert naa > now


# ── Test 3: Exhausted attempts → DEAD ─────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.integration
async def test_row_becomes_dead_at_max_attempts(maker: async_sessionmaker):
    async with maker() as s:
        inc_id = await _seed_incident(s)
        # attempts already at max_attempts - 1 so one more run exhausts it
        ob_id = await _seed_outbox(s, inc_id, max_attempts=2, attempts=1)
        await s.commit()

    failing = AsyncMock()
    failing.send = AsyncMock(
        return_value=ProviderResult(sid=None, status="failed", channel="whatsapp")
    )
    chain = ProviderChain([failing])

    await run_once(maker, chain)

    async with maker() as s:
        ob = (await s.execute(select(Outbox).where(Outbox.id == ob_id))).scalar_one()
    assert ob.status == "DEAD"


# ── Test 4: Two PENDING rows → both delivered ──────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.integration
async def test_two_pending_rows_both_delivered(maker: async_sessionmaker):
    async with maker() as s:
        inc1 = await _seed_incident(s)
        inc2 = await _seed_incident(s)
        ob1 = await _seed_outbox(s, inc1)
        ob2 = await _seed_outbox(s, inc2)
        await s.commit()

    chain = ProviderChain([ConsoleProvider()])
    processed = await run_once(maker, chain)
    assert processed >= 2

    async with maker() as s:
        for ob_id in (ob1, ob2):
            ob = (await s.execute(select(Outbox).where(Outbox.id == ob_id))).scalar_one()
            assert ob.status == "SENT"


# ── Test 5: Future next_attempt_at is skipped ──────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.integration
async def test_future_next_attempt_at_is_skipped(maker: async_sessionmaker):
    future = datetime.now(tz=UTC) + timedelta(hours=1)
    async with maker() as s:
        inc_id = await _seed_incident(s)
        ob_id = await _seed_outbox(s, inc_id, next_attempt_at=future)
        await s.commit()

    chain = ProviderChain([ConsoleProvider()])
    # run_once must not touch this row
    async with maker() as s:
        before = (await s.execute(select(Outbox).where(Outbox.id == ob_id))).scalar_one()
        assert before.status == "PENDING"

    await run_once(maker, chain)

    async with maker() as s:
        after = (await s.execute(select(Outbox).where(Outbox.id == ob_id))).scalar_one()
    assert after.status == "PENDING"
    assert after.attempts == 0


# ── Test 6: Concurrent relay replicas → exactly one send, one messages row ────────

@pytest.mark.asyncio
@pytest.mark.integration
async def test_concurrent_relay_replicas_send_exactly_once(maker: async_sessionmaker):
    """Two concurrent run_once passes race to claim the same PENDING row.

    The two-phase claim (UPDATE ... SET status='SENDING' over FOR UPDATE SKIP LOCKED
    rows) means only one pass claims the row; the other claims nothing. Exactly one
    send, exactly one messages row.
    """
    async with maker() as s:
        inc_id = await _seed_incident(s)
        ob_id = await _seed_outbox(s, inc_id)
        await s.commit()

    chain = ProviderChain([ConsoleProvider()])

    await asyncio.gather(
        run_once(maker, chain, worker_id="A"),
        run_once(maker, chain, worker_id="B"),
    )

    async with maker() as s:
        ob = (await s.execute(select(Outbox).where(Outbox.id == ob_id))).scalar_one()
        assert ob.status == "SENT"
        assert ob.claimed_by is None
        msgs = (
            await s.execute(select(Message).where(Message.incident_id == inc_id))
        ).scalars().all()
        assert len(msgs) == 1, f"expected exactly 1 messages row, got {len(msgs)}"


# ── Test 7: A row left in SENDING with an expired lease is reclaimed and sent ──────

@pytest.mark.asyncio
@pytest.mark.integration
async def test_stale_sending_row_is_reclaimed(maker: async_sessionmaker):
    """A SENDING row whose lease expired (a crashed in-flight send) is reclaimed."""
    async with maker() as s:
        inc_id = await _seed_incident(s)
        ob_id = await _seed_outbox(s, inc_id)
        # Force the row into SENDING with a lease that already expired.
        await s.execute(
            text(
                "UPDATE outbox SET status='SENDING', claimed_by='dead', "
                "claimed_until = now() - interval '5 seconds' WHERE id = :id"
            ),
            {"id": str(ob_id)},
        )
        await s.commit()

    chain = ProviderChain([ConsoleProvider()])
    processed = await run_once(maker, chain, worker_id="reaper", lease_seconds=30)
    assert processed == 1

    async with maker() as s:
        ob = (await s.execute(select(Outbox).where(Outbox.id == ob_id))).scalar_one()
    assert ob.status == "SENT"
    assert ob.claimed_by is None


# ── Test 8: notifier.send span is emitted with outbox_id attribute ─────────────

@pytest.mark.asyncio
@pytest.mark.integration
async def test_notifier_send_emits_span(maker: async_sessionmaker):
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from cloud.common.telemetry import reset_telemetry, setup_telemetry

    exporter = InMemorySpanExporter()
    reset_telemetry()
    setup_telemetry("notif-test", exporter=exporter)

    async with maker() as s:
        inc_id = await _seed_incident(s)
        ob_id = await _seed_outbox(s, inc_id)
        await s.commit()

    await run_once(maker, ProviderChain([ConsoleProvider()]))

    spans = [s for s in exporter.get_finished_spans() if s.name == "notifier.send"]
    assert spans, "expected a notifier.send span"
    assert spans[0].attributes["outbox_id"] == str(ob_id)


# ── Test 9: successful send increments notifier_sends_total + histogram ─────────

@pytest.mark.asyncio
@pytest.mark.integration
async def test_notifier_metrics_recorded(maker: async_sessionmaker):
    from cloud.common.metrics import REGISTRY

    async with maker() as s:
        inc_id = await _seed_incident(s)
        await _seed_outbox(s, inc_id)
        await s.commit()
    sent_before = REGISTRY.get_sample_value(
        "notifier_sends_total", {"channel": "console", "result": "sent"}
    ) or 0.0
    await run_once(maker, ProviderChain([ConsoleProvider()]))
    sent_after = REGISTRY.get_sample_value(
        "notifier_sends_total", {"channel": "console", "result": "sent"}
    )
    send_count = REGISTRY.get_sample_value(
        "notifier_send_seconds_count", {"channel": "console"}
    )
    assert sent_after == sent_before + 1
    assert send_count >= 1


# ── Test 10: DEAD row increments dead literal + provider_send_failures_total ────

@pytest.mark.asyncio
@pytest.mark.integration
async def test_notifier_dead_result_increments(maker: async_sessionmaker):
    from cloud.common.metrics import REGISTRY

    async with maker() as s:
        inc_id = await _seed_incident(s)
        await _seed_outbox(s, inc_id, max_attempts=1)  # one failing attempt → DEAD
        await s.commit()
    failing = AsyncMock()
    failing.send = AsyncMock(
        return_value=ProviderResult(sid=None, status="failed", channel="whatsapp")
    )
    dead_before = REGISTRY.get_sample_value(
        "notifier_sends_total", {"channel": "whatsapp", "result": "dead"}
    ) or 0.0
    await run_once(maker, ProviderChain([failing]))
    dead_after = REGISTRY.get_sample_value(
        "notifier_sends_total", {"channel": "whatsapp", "result": "dead"}
    )
    fail_after = REGISTRY.get_sample_value(
        "provider_send_failures_total", {"provider": "whatsapp"}
    )
    assert dead_after == dead_before + 1  # the LITERAL 'dead' — not result.status
    assert fail_after >= 1
