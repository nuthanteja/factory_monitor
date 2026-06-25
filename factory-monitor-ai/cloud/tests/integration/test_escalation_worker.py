"""Integration tests for the escalation worker poll loop.

Tests seed due incidents and call poll_once() directly against real Postgres
(testcontainers), asserting the full thread fires + claim/lease is cleared.
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

from cloud.common.db.models import (
    EscalationIdempotency,
    Incident,
    IncidentEvent,
    IncidentStatus,
    Outbox,
)
from cloud.common.seed_demo import seed_demo_roster, seed_demo_tiers
from cloud.escalation_worker.worker import EscalationWorker, poll_once, poll_once_ids

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
async def maker(migrated_url: str) -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(migrated_url, future=True)
    m = async_sessionmaker(engine, expire_on_commit=False)
    await seed_demo_roster(m)
    await seed_demo_tiers(m, site_id="plant-01", delay_seconds=5)
    yield m
    await engine.dispose()


async def _insert_due_incident(
    maker: async_sessionmaker, status: IncidentStatus, tier: int
) -> Incident:
    now = datetime.now(UTC)
    inc = Incident(
        id=uuid.uuid4(),
        site_id="plant-01",
        camera_id="cam_01",
        zone_id="zone_weld_bay",
        anomaly_type="ppe_no_hardhat",
        rule_id="PPE_NO_HARDHAT",
        object_class="person",
        track_id=f"cam_01:{uuid.uuid4().hex[:6]}",
        severity="high",
        dedup_key=f"worker|{uuid.uuid4().hex}|PPE|bucket",
        status=status,
        current_tier=tier,
        next_fire_at=now - timedelta(seconds=10),  # overdue
        deadline_at=now - timedelta(seconds=10),
        is_synthetic=False,
    )
    async with maker() as s:
        s.add(inc)
        await s.commit()
        await s.refresh(inc)
    return inc


@pytest.mark.asyncio
async def test_poll_once_fires_due_incident(maker):
    inc = await _insert_due_incident(maker, IncidentStatus.AWAITING_OPERATOR, 0)
    worker_id = f"worker-test-{uuid.uuid4().hex[:8]}"

    processed = await poll_once(maker, worker_id=worker_id, lease_seconds=30, batch=10)

    assert processed >= 1

    async with maker() as s:
        updated = await s.get(Incident, inc.id)
    assert updated.status == IncidentStatus.TIER1
    assert updated.current_tier == 1
    assert updated.claimed_by is None   # claim released after transition commit


@pytest.mark.asyncio
async def test_poll_once_does_not_process_non_due_incidents(maker):
    now = datetime.now(UTC)
    inc = Incident(
        id=uuid.uuid4(),
        site_id="plant-01",
        camera_id="cam_01",
        zone_id=None,
        anomaly_type="ppe_no_hardhat",
        rule_id="PPE_NO_HARDHAT",
        object_class="person",
        track_id="cam_01:future",
        severity="low",
        dedup_key=f"future|{uuid.uuid4().hex}|PPE|bucket",
        status=IncidentStatus.AWAITING_OPERATOR,
        current_tier=0,
        next_fire_at=now + timedelta(hours=1),  # NOT due
        is_synthetic=False,
    )
    async with maker() as s:
        s.add(inc)
        await s.commit()

    await poll_once(maker, worker_id="worker-future", lease_seconds=30, batch=10)
    # We only assert the future incident wasn't touched; processed may include others
    async with maker() as s:
        still_awaiting = await s.get(Incident, inc.id)
    assert still_awaiting.status == IncidentStatus.AWAITING_OPERATOR


@pytest.mark.asyncio
async def test_poll_once_no_duplicate_fire_on_concurrent_calls(maker):
    """Two concurrent poll_once calls on the same incident must fire it exactly once."""
    inc = await _insert_due_incident(maker, IncidentStatus.AWAITING_OPERATOR, 0)

    # Run two concurrent polls — SKIP LOCKED + idempotency guard ensure exactly one fires
    w1 = "worker-concurrent-A"
    w2 = "worker-concurrent-B"
    results = await asyncio.gather(
        poll_once(maker, worker_id=w1, lease_seconds=30, batch=10),
        poll_once(maker, worker_id=w2, lease_seconds=30, batch=10),
    )
    _ = sum(results)

    # Exactly one TIER1_FIRED event for this incident
    async with maker() as s:
        events = (
            await s.execute(
                select(IncidentEvent).where(
                    IncidentEvent.incident_id == inc.id,
                    IncidentEvent.type == "TIER1_FIRED",
                )
            )
        ).scalars().all()
    assert len(events) == 1

    # Exactly one escalation_idempotency row
    async with maker() as s:
        idemp = (
            await s.execute(
                select(EscalationIdempotency).where(
                    EscalationIdempotency.incident_id == inc.id
                )
            )
        ).scalars().all()
    assert len(idemp) == 1


@pytest.mark.asyncio
async def test_escalation_worker_runs_and_stops(maker):
    """EscalationWorker.run_until_stopped exits cleanly when stop() is called."""
    inc = await _insert_due_incident(maker, IncidentStatus.AWAITING_OPERATOR, 0)

    worker = EscalationWorker(
        session_maker=maker,
        worker_id="worker-stop-test",
        poll_interval_seconds=0.1,
        lease_seconds=30,
        batch=10,
    )
    await worker.start()

    # Let the worker run for up to 2s and then stop it
    run_task = asyncio.create_task(worker.run_until_stopped())
    await asyncio.sleep(0.5)
    await worker.stop()
    await asyncio.wait_for(run_task, timeout=3.0)

    # The incident should have advanced (worker fired it in the 0.5s window)
    async with maker() as s:
        updated = await s.get(Incident, inc.id)
    assert updated.status == IncidentStatus.TIER1


@pytest.mark.asyncio
async def test_poll_once_skips_resolved_incident(maker):
    """Worker must NOT advance an incident that was resolved/acked between the claim
    commit and the transition transaction.

    Simulates the ack-clobber race: the incident is seeded as due, then its
    status is flipped to RESOLVED (next_fire_at=NULL) before poll_once runs,
    mimicking an operator ACK that landed after the claim window opened but
    before the transition txn acquired the FOR UPDATE lock.
    """
    inc = await _insert_due_incident(maker, IncidentStatus.AWAITING_OPERATOR, 0)

    # Simulate ACK arriving before the worker's transition txn
    operator_id = uuid.uuid4()
    async with maker() as s:
        await s.execute(
            text(
                "UPDATE incidents SET status = 'RESOLVED', next_fire_at = NULL, "
                "resolved_by = :op, resolved_at = now() WHERE id = :id"
            ),
            {"op": operator_id, "id": inc.id},
        )
        await s.commit()

    await poll_once(maker, worker_id="worker-ack-race", lease_seconds=30, batch=10)

    async with maker() as s:
        after = await s.get(Incident, inc.id)
        events = (
            await s.execute(
                select(IncidentEvent).where(IncidentEvent.incident_id == inc.id)
            )
        ).scalars().all()
        outbox_rows = (
            await s.execute(
                select(Outbox).where(Outbox.incident_id == inc.id)
            )
        ).scalars().all()

    # Incident must remain RESOLVED, tier unchanged, no escalation side-effects
    assert after.status == IncidentStatus.RESOLVED
    assert after.current_tier == 0
    assert after.next_fire_at is None
    assert len(events) == 0, "no audit events should be written for a resolved incident"
    assert len(outbox_rows) == 0, "no outbox rows should be written for a resolved incident"


@pytest.mark.asyncio
async def test_fault_hook_invoked_with_incident_id_before_commit(maker):
    """The chaos seam fires with the incident id; a non-blocking hook still commits."""
    inc = await _insert_due_incident(maker, IncidentStatus.AWAITING_OPERATOR, 0)
    seen: list[uuid.UUID] = []

    async def hook(incident_id: uuid.UUID) -> None:
        seen.append(incident_id)

    fired = await poll_once_ids(
        maker, worker_id="seam", lease_seconds=30, batch=10, fault_hook=hook
    )

    assert inc.id in seen
    assert inc.id in fired
    async with maker() as s:
        assert (await s.get(Incident, inc.id)).status == IncidentStatus.TIER1


@pytest.mark.asyncio
async def test_escalation_metrics_recorded(maker):
    from cloud.common.metrics import REGISTRY
    await _insert_due_incident(maker, IncidentStatus.AWAITING_OPERATOR, 0)
    fired_before = REGISTRY.get_sample_value(
        "escalations_fired_total", {"tier": "1", "result": "fired"}
    ) or 0.0
    claim_before = REGISTRY.get_sample_value("escalation_claim_latency_seconds_count") or 0.0

    await poll_once(maker, worker_id="metrics", lease_seconds=30, batch=10)

    fired_after = REGISTRY.get_sample_value(
        "escalations_fired_total", {"tier": "1", "result": "fired"}
    )
    lag_after = REGISTRY.get_sample_value("escalation_fire_lag_seconds_count", {"tier": "1"})
    claim_after = REGISTRY.get_sample_value("escalation_claim_latency_seconds_count")
    assert fired_after == fired_before + 1          # tier label is the STRING "1"
    assert lag_after >= 1
    assert claim_after == claim_before + 1          # one batch timed


@pytest.mark.asyncio
async def test_escalation_transition_emits_span(maker):
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from cloud.common.telemetry import reset_telemetry, setup_telemetry

    exporter = InMemorySpanExporter()
    reset_telemetry()
    setup_telemetry("esc-test", exporter=exporter)

    inc = await _insert_due_incident(maker, IncidentStatus.AWAITING_OPERATOR, 0)
    await poll_once(maker, worker_id="span", lease_seconds=30, batch=10)

    spans = [s for s in exporter.get_finished_spans() if s.name == "escalation.transition"]
    assert spans, "expected an escalation.transition span"
    assert spans[0].attributes["incident_id"] == str(inc.id)
    assert spans[0].attributes["tier"] == 1
