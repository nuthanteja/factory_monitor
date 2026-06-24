"""Chaos: notifier exactly-once-EFFECT send under a crash between send and settle.

A worker claims an outbox row into SENDING, the provider delivers the message,
then the worker dies BEFORE committing SENT. The row is reclaimed after its lease
and re-sent. An idempotent receiver (dedup on idempotency_key — mirrors Twilio's
Idempotency-Key) collapses the re-send into ONE delivered effect. The send is
at-least-once at the *invocation* level (2 calls) but exactly-once at the *effect*
level (1 delivered key, 1 messages row).
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from cloud.common.db.models import Message, Outbox
from cloud.notifications.chain import ProviderChain
from cloud.notifications.provider import NotificationKind, ProviderResult
from cloud.notifier_worker.relay import run_once

MIGRATIONS = str(Path(__file__).resolve().parents[3] / "cloud" / "migrations")


def _async_url(sync_url: str) -> str:
    return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://").replace(
        "postgresql://", "postgresql+asyncpg://"
    )


@pytest.fixture(scope="module")
def pg_container():
    with PostgresContainer("postgres:16") as pg:
        yield pg


@pytest.fixture(scope="module")
def migrated_url(pg_container: PostgresContainer) -> str:
    sync_url = pg_container.get_connection_url()
    cfg = Config()
    cfg.set_main_option("script_location", MIGRATIONS)
    cfg.set_main_option("sqlalchemy.url", sync_url)
    command.upgrade(cfg, "head")
    return _async_url(sync_url)


@pytest_asyncio.fixture
async def maker(migrated_url: str):
    engine = create_async_engine(migrated_url, future=True)
    m = async_sessionmaker(engine, expire_on_commit=False)
    yield m
    await engine.dispose()


class RecordingIdempotentProvider:
    """Mock provider that dedups on idempotency_key (like Twilio's Idempotency-Key).

    invocations: every send() call (may exceed 1 across a crash → at-least-once).
    delivered:   idempotency_key -> sid, written once (exactly-once effect).
    """

    def __init__(self, *, channel: str = "whatsapp") -> None:
        self.invocations: list[str] = []
        self.delivered: dict[str, str] = {}
        self._channel = channel

    async def send(
        self,
        to: str,
        kind: NotificationKind,
        *,
        template_name: str | None = None,
        variables: dict | None = None,
        body: str | None = None,
        idempotency_key: str,
    ) -> ProviderResult:
        self.invocations.append(idempotency_key)
        sid = self.delivered.get(idempotency_key)
        if sid is None:
            sid = f"sid-{idempotency_key}"
            self.delivered[idempotency_key] = sid
        return ProviderResult(sid=sid, status="sent", channel=self._channel)

    async def healthcheck(self) -> bool:
        return True


async def _seed_incident(session: AsyncSession) -> uuid.UUID:
    inc_id = uuid.uuid4()
    now = datetime.now(tz=UTC)
    await session.execute(
        text(
            "INSERT INTO incidents (id, site_id, camera_id, anomaly_type, rule_id, "
            "severity, dedup_key, status, current_tier, next_fire_at, is_synthetic, "
            "created_at, updated_at) VALUES (:id, 'plant-01', 'cam_01', 'ppe_no_hardhat', "
            "'PPE_NO_HARDHAT', 'high', :dk, 'AWAITING_OPERATOR', 0, :nfa, false, now(), now())"
        ),
        {"id": str(inc_id), "dk": f"dk-{inc_id}", "nfa": now + timedelta(seconds=120)},
    )
    return inc_id


async def _seed_outbox(session: AsyncSession, incident_id: uuid.UUID) -> uuid.UUID:
    ob_id = uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO outbox (id, incident_id, tier, to_phone_e164, channel, kind, "
            "template_name, variables, idempotency_key, status, attempts, max_attempts, "
            "next_attempt_at, created_at) VALUES (:id, :inc, 1, '+10000000001', 'whatsapp', "
            "'TEMPLATE', 'floor_manager_alert_v1', '{\"zone\":\"weld_bay\"}', :idem, 'PENDING', "
            "0, 6, now(), now())"
        ),
        {"id": str(ob_id), "inc": str(incident_id), "idem": f"{incident_id}|1"},
    )
    return ob_id


@pytest.mark.chaos
@pytest.mark.integration
@pytest.mark.asyncio
async def test_send_is_exactly_once_effect_under_crash(maker: async_sessionmaker):
    async with maker() as s:
        inc_id = await _seed_incident(s)
        ob_id = await _seed_outbox(s, inc_id)
        await s.commit()

    provider = RecordingIdempotentProvider()
    chain = ProviderChain([provider])

    # Worker A: crash AFTER the provider delivers, BEFORE the settle commit.
    async def crash_hook(_outbox_id: uuid.UUID) -> None:
        raise RuntimeError("killed mid-settle")

    with pytest.raises(RuntimeError):
        await run_once(maker, chain, worker_id="A", lease_seconds=1, fault_hook=crash_hook)

    # The row is stuck SENDING; the provider delivered exactly once already.
    async with maker() as s:
        ob = (await s.execute(select(Outbox).where(Outbox.id == ob_id))).scalar_one()
    assert ob.status == "SENDING"
    assert set(provider.delivered) == {str(ob_id)}
    assert len(provider.invocations) == 1

    # Wait out the 1s lease, then a healthy worker B reclaims + settles.
    await asyncio.sleep(1.2)
    processed = await run_once(maker, chain, worker_id="B", lease_seconds=30)
    assert processed == 1

    async with maker() as s:
        ob = (await s.execute(select(Outbox).where(Outbox.id == ob_id))).scalar_one()
        msgs = (
            await s.execute(select(Message).where(Message.incident_id == inc_id))
        ).scalars().all()

    assert ob.status == "SENT"
    assert ob.claimed_by is None
    # Exactly-once EFFECT: one delivered key, one messages row...
    assert set(provider.delivered) == {str(ob_id)}
    assert len(msgs) == 1
    # ...despite at-least-once INVOCATION (the crash forced a second send call).
    assert len(provider.invocations) == 2
