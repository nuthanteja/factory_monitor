"""Integration e2e: ingest creates outbox row → relay delivers it → SENT + messages.

This test wires together:
  create_incident_from_anomaly  (Phase 1 ingest service)
  run_once                      (Task 14 relay)
  ConsoleProvider               (Task 10 default provider)

It asserts:
  1. After ingest: one PENDING outbox row exists.
  2. After run_once: outbox is SENT, one messages(direction='out') row exists.
  3. A second run_once does NOT re-deliver (row is already SENT, not PENDING).
  4. Idempotency: inserting a second outbox row with the same idempotency_key
     (UNIQUE outbox.idempotency_key) is rejected at the DB level — only one
     delivery per (incident, tier).

ADAPTATION: The brief's verbatim test seeds the outbox manually.  This test
instead calls create_incident_from_anomaly with on_call_resolver=resolve (the
real ingest→outbox path, Task 5) so the relay smoke proves the full
ingest→outbox→relay→messages chain on a real DB without a manual INSERT.
The demo roster + tiers are seeded first (seed_demo_roster / seed_demo_tiers).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from testcontainers.postgres import PostgresContainer
from alembic import command
from alembic.config import Config

from cloud.common.db.models import Incident, IncidentEvent, Message, Outbox
from cloud.common.on_call_resolver import resolve
from cloud.common.schemas.anomaly import AnomalyEvent
from cloud.common.seed_demo import seed_demo_roster, seed_demo_tiers
from cloud.ingest_worker.service import create_incident_from_anomaly
from cloud.notifications.chain import ProviderChain
from cloud.notifications.console import ConsoleProvider
from cloud.notifier_worker.relay import run_once

MIGRATIONS = str(Path(__file__).resolve().parents[3] / "cloud" / "migrations")


def _async_url(s: str) -> str:
    return s.replace("postgresql+psycopg2://", "postgresql+asyncpg://").replace(
        "postgresql://", "postgresql+asyncpg://"
    )


def _make_anomaly(**overrides) -> AnomalyEvent:
    base = dict(
        schema_version="1.0",
        event_id=str(uuid.uuid4()),
        anomaly_type="ppe_no_hardhat",
        rule_id="PPE_NO_HARDHAT",
        occurred_at=datetime(2026, 6, 23, 10, 0, 0, tzinfo=timezone.utc),
        site_id="plant-01",
        camera_id="cam_01",
        zone_id="zone_weld_bay",
        track_id="cam_01:1",
        object_class="person",
        severity="high",
        confidence=0.9,
        dedup_key=f"cam_01|cam_01:1|PPE_NO_HARDHAT|{uuid.uuid4().hex[:8]}",
        evidence={"bbox": [0, 0, 100, 100], "snapshot_url": "", "footage_source": ""},
        source="edge",
    )
    base.update(overrides)
    return AnomalyEvent(**base)


@pytest.fixture(scope="module")
def pg_url():
    with PostgresContainer("postgres:16") as pg:
        sync = pg.get_connection_url()
        cfg = Config()
        cfg.set_main_option("script_location", MIGRATIONS)
        cfg.set_main_option("sqlalchemy.url", sync)
        command.upgrade(cfg, "head")
        yield _async_url(sync)


@pytest.fixture
async def maker(pg_url: str):
    engine = create_async_engine(pg_url, future=True)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    # Seed roster + tiers so on_call_resolver finds the demo operator.
    await seed_demo_roster(sm)
    await seed_demo_tiers(sm, site_id="plant-01", delay_seconds=5)
    yield sm
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_ingest_then_relay_full_thread(maker: async_sessionmaker):
    """Incident created via ingest → outbox seeded atomically → relay delivers → SENT + messages row."""
    anomaly = _make_anomaly()

    # 1. Ingest: create incident with resolver — enqueues tier-0 PENDING outbox atomically.
    async with maker() as s:
        result = await create_incident_from_anomaly(
            s, anomaly, grace_seconds=120, on_call_resolver=resolve
        )
        await s.commit()

    assert result.created is True
    incident_id = result.incident_id

    # 2. Before relay: exactly one PENDING outbox row for the incident.
    async with maker() as s:
        outbox_rows = (
            await s.execute(select(Outbox).where(Outbox.incident_id == incident_id))
        ).scalars().all()
    assert len(outbox_rows) == 1
    ob_id = outbox_rows[0].id
    assert outbox_rows[0].status == "PENDING"
    assert outbox_rows[0].to_phone_e164 == "+15550000001"  # Demo Operator from seed

    # 3. Run relay once.
    chain = ProviderChain([ConsoleProvider()])
    count = await run_once(maker, chain)
    assert count >= 1

    # 4. After relay: outbox row is SENT.
    async with maker() as s:
        ob_after = (await s.execute(select(Outbox).where(Outbox.id == ob_id))).scalar_one()
    assert ob_after.status == "SENT"
    assert ob_after.sent_at is not None

    # 5. messages(direction='out') row exists for the operator's phone.
    async with maker() as s:
        msgs = (
            await s.execute(
                select(Message).where(Message.incident_id == incident_id)
            )
        ).scalars().all()
    assert len(msgs) == 1
    assert msgs[0].direction == "out"
    assert msgs[0].to_phone_e164 == "+15550000001"
    assert msgs[0].status == "sent"

    # 6. Second run_once does NOT re-deliver (row is SENT, not PENDING).
    count2 = await run_once(maker, chain)
    async with maker() as s:
        msgs2 = (
            await s.execute(
                select(Message).where(Message.incident_id == incident_id)
            )
        ).scalars().all()
    assert len(msgs2) == 1  # still exactly one — no duplicate delivery


@pytest.mark.asyncio
@pytest.mark.integration
async def test_duplicate_outbox_idempotency_key_rejected(maker: async_sessionmaker):
    """UNIQUE outbox.idempotency_key collapses duplicate outbox inserts."""
    anomaly = _make_anomaly()
    async with maker() as s:
        result = await create_incident_from_anomaly(s, anomaly, grace_seconds=120)
        await s.commit()

    incident_id = result.incident_id
    idem_key = f"{incident_id}|tier0-dedup"

    # First insert succeeds
    async with maker() as s:
        await s.execute(
            text(
                """
                INSERT INTO outbox
                  (id, incident_id, tier, to_phone_e164, channel, kind,
                   template_name, idempotency_key, status, attempts, max_attempts,
                   next_attempt_at, created_at)
                VALUES
                  (:id, :inc, 0, '+1', 'console', 'TEMPLATE',
                   'alert_operator', :idem, 'PENDING', 0, 6, now(), now())
                """
            ),
            {"id": str(uuid.uuid4()), "inc": str(incident_id), "idem": idem_key},
        )
        await s.commit()

    # Second insert with same idempotency_key must raise IntegrityError
    with pytest.raises(IntegrityError):
        async with maker() as s:
            await s.execute(
                text(
                    """
                    INSERT INTO outbox
                      (id, incident_id, tier, to_phone_e164, channel, kind,
                       template_name, idempotency_key, status, attempts, max_attempts,
                       next_attempt_at, created_at)
                    VALUES
                      (:id, :inc, 0, '+1', 'console', 'TEMPLATE',
                       'alert_operator', :idem, 'PENDING', 0, 6, now(), now())
                    """
                ),
                {"id": str(uuid.uuid4()), "inc": str(incident_id), "idem": idem_key},
            )
            await s.commit()
