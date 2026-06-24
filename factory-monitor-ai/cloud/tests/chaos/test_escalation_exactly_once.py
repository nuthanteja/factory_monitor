"""Chaos: escalation exactly-once under a worker killed mid-transition.

3 workers run against one due incident; the victim is cancelled while it holds a
claim and has flushed (but not committed) a tier transition. The uncommitted txn
rolls back, the claim persists until the lease expires, and a survivor reclaims +
fires. The escalation_idempotency guard makes any double-fire a no-op.

Asserts 0 duplicate (each tier event exactly once) and 0 miss (reaches CRITICAL).
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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from cloud.common.db.models import (
    EscalationIdempotency,
    Incident,
    IncidentEvent,
    IncidentStatus,
    Outbox,
)
from cloud.common.seed_demo import seed_demo_roster, seed_demo_tiers
from cloud.escalation_worker.worker import EscalationWorker

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
    await seed_demo_roster(m)
    await seed_demo_tiers(m, site_id="plant-01", delay_seconds=1)  # fast thread
    yield m
    await engine.dispose()


async def _seed_due_incident(maker: async_sessionmaker) -> Incident:
    now = datetime.now(UTC)
    inc = Incident(
        id=uuid.uuid4(),
        site_id="plant-01",
        camera_id="cam_01",
        zone_id=None,
        anomaly_type="ppe_no_hardhat",
        rule_id="PPE_NO_HARDHAT",
        object_class="person",
        track_id=f"cam_01:{uuid.uuid4().hex[:6]}",
        severity="high",
        dedup_key=f"chaos|{uuid.uuid4().hex}|PPE|bucket",
        status=IncidentStatus.AWAITING_OPERATOR,
        current_tier=0,
        next_fire_at=now - timedelta(seconds=1),  # due now
        deadline_at=now - timedelta(seconds=1),
        is_synthetic=False,
    )
    async with maker() as s:
        s.add(inc)
        await s.commit()
        await s.refresh(inc)
    return inc


@pytest.mark.chaos
@pytest.mark.integration
@pytest.mark.asyncio
async def test_escalation_exactly_once_under_worker_kill(maker: async_sessionmaker):
    inc = await _seed_due_incident(maker)

    lease = 1
    claimed = asyncio.Event()
    hang = asyncio.Event()  # never set → victim hangs mid-transition

    async def victim_hook(incident_id: uuid.UUID) -> None:
        if incident_id == inc.id:
            claimed.set()
            await hang.wait()  # hold the claim, transition flushed-but-uncommitted

    victim = EscalationWorker(
        maker, worker_id="victim", poll_interval_seconds=0.05,
        lease_seconds=lease, batch=10, fault_hook=victim_hook,
    )
    surv1 = EscalationWorker(
        maker, worker_id="survivor-1", poll_interval_seconds=0.05, lease_seconds=lease, batch=10
    )
    surv2 = EscalationWorker(
        maker, worker_id="survivor-2", poll_interval_seconds=0.05, lease_seconds=lease, batch=10
    )

    # Start the victim ALONE first and wait until it has claimed the incident and is
    # hanging mid-transition — this removes the start-up race so the victim is
    # deterministically the worker that gets killed while holding the claim.
    await victim.start()
    vt = asyncio.create_task(victim.run_until_stopped())
    await asyncio.wait_for(claimed.wait(), timeout=10)

    # Now bring up the survivors and KILL the victim mid-transition.
    await surv1.start()
    await surv2.start()
    t1 = asyncio.create_task(surv1.run_until_stopped())
    t2 = asyncio.create_task(surv2.run_until_stopped())

    try:
        vt.cancel()
        with pytest.raises(asyncio.CancelledError):
            await vt

        # Survivors must reclaim (after the lease) and drive to CRITICAL_UNRESOLVED.
        async def wait_for_critical() -> None:
            while True:
                async with maker() as s:
                    cur = await s.get(Incident, inc.id)
                if cur is not None and cur.status == IncidentStatus.CRITICAL_UNRESOLVED:
                    return
                await asyncio.sleep(0.1)

        await asyncio.wait_for(wait_for_critical(), timeout=25)
    finally:
        await surv1.stop()
        await surv2.stop()
        await asyncio.wait_for(asyncio.gather(t1, t2, return_exceptions=True), timeout=10)
        hang.set()  # release the (already-cancelled) victim hook if needed

    async with maker() as s:
        event_types = (
            await s.execute(
                select(IncidentEvent.type).where(IncidentEvent.incident_id == inc.id)
            )
        ).scalars().all()
        idemp = (
            await s.execute(
                select(EscalationIdempotency).where(
                    EscalationIdempotency.incident_id == inc.id
                )
            )
        ).scalars().all()
        outbox = (
            await s.execute(select(Outbox).where(Outbox.incident_id == inc.id))
        ).scalars().all()
        final = await s.get(Incident, inc.id)

    # 0 duplicate: each tier-fire event appears exactly once.
    fired = sorted(t for t in event_types if t in ("TIER1_FIRED", "TIER2_FIRED", "CRITICAL"))
    assert fired == ["CRITICAL", "TIER1_FIRED", "TIER2_FIRED"], (
        f"unexpected fire events: {event_types}"
    )

    # idempotency rows == fired tiers (1, 2, 3).
    assert len(idemp) == 3

    # 0 miss: the thread completed despite the kill.
    assert final.status == IncidentStatus.CRITICAL_UNRESOLVED
    assert final.claimed_by is None

    # No duplicate sends queued: one outbox row per real recipient tier (1 and 2;
    # CRITICAL has no recipient). The UNIQUE(idempotency_key) also guards this.
    keys = sorted(o.idempotency_key for o in outbox)
    assert keys == [f"{inc.id}|1", f"{inc.id}|2"], f"unexpected outbox keys: {keys}"
