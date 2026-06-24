"""Integration tests for the escalation state-machine transition function.

Tests use a real Postgres (testcontainers) and drive transitions by setting
next_fire_at into the past so no wall-clock sleeping is needed.
"""
from __future__ import annotations

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
from cloud.escalation_worker.transition import TransitionResult, fire_transition

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


async def _insert_incident(
    maker: async_sessionmaker,
    *,
    status: IncidentStatus = IncidentStatus.AWAITING_OPERATOR,
    current_tier: int = 0,
    next_fire_at_offset_seconds: int = -10,  # negative = in the past (due now)
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
        track_id="cam_01:9900",
        severity="high",
        dedup_key=f"test|{uuid.uuid4().hex}|PPE|bucket",
        status=status,
        current_tier=current_tier,
        next_fire_at=now + timedelta(seconds=next_fire_at_offset_seconds),
        deadline_at=now + timedelta(seconds=next_fire_at_offset_seconds),
        is_synthetic=False,
    )
    async with maker() as s:
        s.add(inc)
        await s.commit()
        await s.refresh(inc)
    return inc


# ── AWAITING_OPERATOR → TIER1 ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_awaiting_operator_fires_tier1(maker):
    inc = await _insert_incident(maker, status=IncidentStatus.AWAITING_OPERATOR, current_tier=0)
    async with maker() as s:
        fresh = await s.get(Incident, inc.id)
        result = await fire_transition(s, fresh)
        await s.commit()

    assert isinstance(result, TransitionResult)
    assert result.fired is True
    assert result.skipped_idempotent is False
    assert result.new_status == IncidentStatus.TIER1.value

    async with maker() as s:
        updated = await s.get(Incident, inc.id)
        assert updated.status == IncidentStatus.TIER1
        assert updated.current_tier == 1
        assert updated.next_fire_at is not None  # re-armed for tier2

        # idempotency row inserted at tier=1
        idemp = (
            await s.execute(
                select(EscalationIdempotency).where(
                    EscalationIdempotency.incident_id == inc.id,
                    EscalationIdempotency.tier == 1,
                )
            )
        ).scalar_one()
        assert idemp is not None

        # audit event TIER1_FIRED
        evt = (
            await s.execute(
                select(IncidentEvent).where(
                    IncidentEvent.incident_id == inc.id,
                    IncidentEvent.type == "TIER1_FIRED",
                )
            )
        ).scalar_one()
        assert evt.from_state == "AWAITING_OPERATOR"
        assert evt.to_state == "TIER1"
        assert evt.tier == 1

        # outbox row for FLOOR_MANAGER
        outbox = (
            await s.execute(
                select(Outbox).where(
                    Outbox.incident_id == inc.id,
                    Outbox.tier == 1,
                )
            )
        ).scalar_one()
        assert outbox.to_phone_e164 == "+15550000002"  # Demo Floor Manager from seed
        assert outbox.kind == "TEMPLATE"
        assert outbox.idempotency_key == f"{inc.id}|1"


# ── Exactly-once: re-running transition on same incident is a no-op ─────────

@pytest.mark.asyncio
async def test_transition_is_idempotent_no_duplicate_fire(maker):
    inc = await _insert_incident(maker, status=IncidentStatus.AWAITING_OPERATOR, current_tier=0)

    # First fire
    async with maker() as s:
        fresh = await s.get(Incident, inc.id)
        r1 = await fire_transition(s, fresh)
        await s.commit()

    # Simulate a concurrent worker that read the incident before the first commit:
    # reset the incident back to AWAITING_OPERATOR/tier=0 state while the
    # idempotency row for tier=1 already exists in the DB.
    async with maker() as s:
        await s.execute(
            text(
                "UPDATE incidents SET"
                "  status = 'AWAITING_OPERATOR',"
                "  current_tier = 0,"
                "  next_fire_at = now() - interval '1 second'"
                " WHERE id = :id"
            ),
            {"id": inc.id},
        )
        await s.commit()

    # Second fire attempt — must be a no-op because escalation_idempotency(incident_id, 1) exists
    async with maker() as s:
        fresh2 = await s.get(Incident, inc.id)
        r2 = await fire_transition(s, fresh2)
        await s.commit()

    assert r1.fired is True
    assert r2.skipped_idempotent is True
    assert r2.fired is False

    # Exactly one TIER1_FIRED row
    async with maker() as s:
        count = len(
            (
                await s.execute(
                    select(IncidentEvent).where(
                        IncidentEvent.incident_id == inc.id,
                        IncidentEvent.type == "TIER1_FIRED",
                    )
                )
            ).scalars().all()
        )
    assert count == 1

    # Exactly one outbox row for tier 1
    async with maker() as s:
        ocount = len(
            (
                await s.execute(
                    select(Outbox).where(Outbox.incident_id == inc.id, Outbox.tier == 1)
                )
            ).scalars().all()
        )
    assert ocount == 1


# ── TIER1 → TIER2 ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_tier1_fires_tier2(maker):
    inc = await _insert_incident(maker, status=IncidentStatus.TIER1, current_tier=1)
    async with maker() as s:
        fresh = await s.get(Incident, inc.id)
        result = await fire_transition(s, fresh)
        await s.commit()

    assert result.fired is True
    assert result.new_status == IncidentStatus.TIER2.value

    async with maker() as s:
        updated = await s.get(Incident, inc.id)
        assert updated.status == IncidentStatus.TIER2
        assert updated.current_tier == 2
        assert updated.next_fire_at is not None

        outbox = (
            await s.execute(
                select(Outbox).where(Outbox.incident_id == inc.id, Outbox.tier == 2)
            )
        ).scalar_one()
        assert outbox.to_phone_e164 == "+15550000003"  # Demo Plant Director from seed


# ── TIER2 → CRITICAL_UNRESOLVED ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_tier2_fires_critical_unresolved(maker):
    inc = await _insert_incident(maker, status=IncidentStatus.TIER2, current_tier=2)
    async with maker() as s:
        fresh = await s.get(Incident, inc.id)
        result = await fire_transition(s, fresh)
        await s.commit()

    assert result.fired is True
    assert result.new_status == IncidentStatus.CRITICAL_UNRESOLVED.value

    async with maker() as s:
        updated = await s.get(Incident, inc.id)
        assert updated.status == IncidentStatus.CRITICAL_UNRESOLVED
        assert updated.next_fire_at is None  # terminal — no more timers
        assert updated.current_tier == 3

        evt = (
            await s.execute(
                select(IncidentEvent).where(
                    IncidentEvent.incident_id == inc.id,
                    IncidentEvent.type == "CRITICAL",
                )
            )
        ).scalar_one()
        assert evt is not None

        # CRITICAL is a terminal/dashboard-only state — no outbox page should exist
        outbox_count = len(
            (
                await s.execute(
                    select(Outbox).where(
                        Outbox.incident_id == inc.id,
                        Outbox.tier == 3,
                    )
                )
            ).scalars().all()
        )
        assert outbox_count == 0, "CRITICAL tier must not produce an outbox page"


# ── Full chain: AWAITING_OPERATOR → TIER1 → TIER2 → CRITICAL ───────────────

@pytest.mark.asyncio
async def test_full_escalation_chain(maker):
    inc = await _insert_incident(maker, status=IncidentStatus.AWAITING_OPERATOR, current_tier=0)

    # Step 1: AWAITING_OPERATOR → TIER1
    async with maker() as s:
        fresh = await s.get(Incident, inc.id)
        r1 = await fire_transition(s, fresh)
        await s.commit()
    assert r1.new_status == IncidentStatus.TIER1.value

    # Backdate for step 2
    async with maker() as s:
        await s.execute(
            text("UPDATE incidents SET next_fire_at = now() - interval '1 second' WHERE id = :id"),
            {"id": inc.id},
        )
        await s.commit()

    # Step 2: TIER1 → TIER2
    async with maker() as s:
        fresh = await s.get(Incident, inc.id)
        r2 = await fire_transition(s, fresh)
        await s.commit()
    assert r2.new_status == IncidentStatus.TIER2.value

    # Backdate for step 3
    async with maker() as s:
        await s.execute(
            text("UPDATE incidents SET next_fire_at = now() - interval '1 second' WHERE id = :id"),
            {"id": inc.id},
        )
        await s.commit()

    # Step 3: TIER2 → CRITICAL_UNRESOLVED
    async with maker() as s:
        fresh = await s.get(Incident, inc.id)
        r3 = await fire_transition(s, fresh)
        await s.commit()
    assert r3.new_status == IncidentStatus.CRITICAL_UNRESOLVED.value

    # Verify exactly one idempotency row per tier fired (1, 2, 3)
    async with maker() as s:
        idemp_rows = (
            await s.execute(
                select(EscalationIdempotency).where(EscalationIdempotency.incident_id == inc.id)
            )
        ).scalars().all()
    assert len(idemp_rows) == 3
    fired_tiers = {r.tier for r in idemp_rows}
    assert fired_tiers == {1, 2, 3}

    # Final state: CRITICAL_UNRESOLVED, next_fire_at=NULL
    async with maker() as s:
        final = await s.get(Incident, inc.id)
    assert final.status == IncidentStatus.CRITICAL_UNRESOLVED
    assert final.next_fire_at is None
