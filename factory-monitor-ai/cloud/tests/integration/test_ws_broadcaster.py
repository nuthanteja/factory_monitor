"""Integration tests for cloud.common.ws.broadcaster.

The broadcaster re-reads the incident from Postgres and calls
manager.broadcast(WsType, data) — the ConnectionManager owns envelope framing
and per-connection seq (broadcaster does NOT build the envelope).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from cloud.common.db.models import Incident, IncidentStatus
from cloud.common.ws.broadcaster import broadcast_change
from cloud.common.ws.contract import WsType
from cloud.common.ws_events import (
    CHANGE_CREATED,
    CHANGE_RESOLVED,
    CHANGE_TIER_ADVANCED,
    CHANGE_UPDATED,
    incident_change,
)

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


class FakeManager:
    """Stand-in for ConnectionManager — records broadcast(WsType, data) calls."""

    def __init__(self) -> None:
        self.calls: list[tuple[WsType, dict]] = []

    async def broadcast(self, type: WsType, data: dict) -> int:  # noqa: A002
        self.calls.append((type, data))
        return 1


async def _insert(
    maker: async_sessionmaker,
    *,
    status: IncidentStatus = IncidentStatus.TIER1,
    tier: int = 1,
    deadline_offset: int = 300,
) -> Incident:
    now = datetime.now(UTC)
    inc = Incident(
        id=uuid.uuid4(),
        site_id="plant-01",
        camera_id="cam_03",
        zone_id="zone_weld_bay",
        anomaly_type="ppe_no_hardhat",
        rule_id="PPE_NO_HARDHAT",
        object_class="person",
        track_id="cam_03:7",
        severity="high",
        dedup_key=f"k|{uuid.uuid4().hex}|PPE|b",
        status=status,
        current_tier=tier,
        next_fire_at=now + timedelta(seconds=deadline_offset),
        deadline_at=now + timedelta(seconds=deadline_offset),
        snapshot_url="s3://evidence/x.jpg",
        is_synthetic=False,
    )
    async with maker() as s:
        s.add(inc)
        await s.commit()
        await s.refresh(inc)
    return inc


# ---------------------------------------------------------------------------
# Correct manager interface
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_created_calls_broadcast_with_ws_type_and_data(maker: async_sessionmaker):
    """broadcast_change calls manager.broadcast(WsType, data) — not broadcast(envelope)."""
    inc = await _insert(maker, status=IncidentStatus.AWAITING_OPERATOR, tier=0)
    mgr = FakeManager()

    count = await broadcast_change(maker, mgr, incident_change(CHANGE_CREATED, inc.id))

    assert count == 1
    assert len(mgr.calls) == 1
    ws_type, data = mgr.calls[0]
    assert ws_type is WsType.INCIDENT_CREATED
    # data is an IncidentView dict — not an envelope (no "seq", no "server_now")
    assert "seq" not in data
    assert "server_now" not in data
    assert data["incident_id"] == str(inc.id)
    assert data["camera_id"] == "cam_03"
    assert data["rule_id"] == "PPE_NO_HARDHAT"
    assert data["status"] == "AWAITING_OPERATOR"
    assert data["current_tier"] == 0


@pytest.mark.asyncio
async def test_updated_calls_broadcast_with_incident_view(maker: async_sessionmaker):
    inc = await _insert(maker, status=IncidentStatus.TIER2, tier=2)
    mgr = FakeManager()

    count = await broadcast_change(maker, mgr, incident_change(CHANGE_UPDATED, inc.id))

    assert count == 1
    ws_type, data = mgr.calls[0]
    assert ws_type is WsType.INCIDENT_UPDATED
    assert data["incident_id"] == str(inc.id)
    assert data["current_tier"] == 2


@pytest.mark.asyncio
async def test_tier_advanced_re_reads_db_truth(maker: async_sessionmaker):
    """Compact event carries stale tier=1 hint; broadcaster must re-read (tier=2 in DB)."""
    inc = await _insert(maker, status=IncidentStatus.TIER2, tier=2)
    mgr = FakeManager()

    # Deliberately pass stale hint in the compact event
    count = await broadcast_change(
        maker, mgr, incident_change(CHANGE_TIER_ADVANCED, inc.id, current_tier=1)
    )

    assert count == 1
    ws_type, data = mgr.calls[0]
    assert ws_type is WsType.INCIDENT_TIER_ADVANCED
    # Must reflect DB truth (tier=2), not the stale hint (tier=1)
    assert data == {
        "incident_id": str(inc.id),
        "current_tier": 2,
        "status": "TIER2",
        "deadline_at": inc.deadline_at.isoformat(),
    }


@pytest.mark.asyncio
async def test_resolved_broadcasts_resolved_subpayload(maker: async_sessionmaker):
    inc = await _insert(maker, status=IncidentStatus.AWAITING_OPERATOR, tier=0)
    async with maker() as s:
        row = await s.get(Incident, inc.id)
        row.status = IncidentStatus.RESOLVED
        row.resolved_at = datetime.now(UTC)
        row.next_fire_at = None
        row.deadline_at = None
        await s.commit()

    mgr = FakeManager()
    count = await broadcast_change(maker, mgr, incident_change(CHANGE_RESOLVED, inc.id))

    assert count == 1
    ws_type, data = mgr.calls[0]
    assert ws_type is WsType.INCIDENT_RESOLVED
    assert data["incident_id"] == str(inc.id)
    assert data["resolved_at"] is not None
    assert "resolved_by" in data


@pytest.mark.asyncio
async def test_missing_incident_returns_zero_no_broadcast(maker: async_sessionmaker):
    """If the incident was deleted (race), broadcast_change returns 0 and never calls manager."""
    mgr = FakeManager()
    count = await broadcast_change(
        maker, mgr, incident_change(CHANGE_CREATED, uuid.uuid4())
    )
    assert count == 0
    assert mgr.calls == []
