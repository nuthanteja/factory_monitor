"""Unit tests for cloud.heatmap_worker.consumer.handle_heatmap."""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest

from cloud.heatmap_worker.consumer import handle_heatmap

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeSession:
    def __init__(self) -> None:
        self.added: list = []
        self.committed = False

    def add_all(self, rows: list) -> None:
        self.added.extend(rows)

    async def commit(self) -> None:
        self.committed = True


class _FakeSessionMaker:
    def __init__(self) -> None:
        self.session = _FakeSession()

    @asynccontextmanager
    async def __call__(self):  # noqa: ANN204
        yield self.session


class _FakeRedis:
    def __init__(self) -> None:
        self.publishes: list[tuple[str, bytes | str]] = []

    async def publish(self, channel: str, message: bytes | str) -> None:
        self.publishes.append((channel, message))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

CAMERA_ID = "cam_01"
ZONE_ID_A = "zone_floor"
ZONE_ID_B = "zone_dock"
TS_STR = "2026-06-25T10:00:00Z"
CHANNEL = "dashboard:heatmap"

GOOD_PAYLOAD = {
    "camera_id": CAMERA_ID,
    "ts": TS_STR,
    "cells": [
        {"zone_id": ZONE_ID_A, "count": 3},
        {"zone_id": ZONE_ID_B, "count": 7},
    ],
}


@pytest.mark.asyncio
async def test_handle_heatmap_inserts_one_row_per_cell() -> None:
    """A valid tick inserts one DensitySnapshot per cell with correct fields."""
    maker = _FakeSessionMaker()
    redis = _FakeRedis()
    raw = json.dumps(GOOD_PAYLOAD).encode()

    await handle_heatmap(maker, raw, redis=redis, channel=CHANNEL)

    rows = maker.session.added
    assert len(rows) == 2  # noqa: PLR2004

    by_zone = {r.zone_id: r for r in rows}
    assert set(by_zone) == {ZONE_ID_A, ZONE_ID_B}

    row_a = by_zone[ZONE_ID_A]
    assert row_a.camera_id == CAMERA_ID
    assert row_a.count == 3
    # ts must be an aware datetime equal to the ISO string
    expected_ts = datetime(2026, 6, 25, 10, 0, 0, tzinfo=UTC)
    assert row_a.ts == expected_ts

    row_b = by_zone[ZONE_ID_B]
    assert row_b.count == 7


@pytest.mark.asyncio
async def test_handle_heatmap_commits_before_publish() -> None:
    """DB commit must happen, then payload is published to the Redis channel."""
    maker = _FakeSessionMaker()
    redis = _FakeRedis()
    raw = json.dumps(GOOD_PAYLOAD).encode()

    await handle_heatmap(maker, raw, redis=redis, channel=CHANNEL)

    assert maker.session.committed
    assert len(redis.publishes) == 1
    ch, msg = redis.publishes[0]
    assert ch == CHANNEL
    # The raw bytes (or equivalent JSON) should be republished
    published = json.loads(msg)
    assert published["camera_id"] == CAMERA_ID
    assert len(published["cells"]) == 2  # noqa: PLR2004


@pytest.mark.asyncio
async def test_handle_heatmap_skips_bad_json() -> None:
    """Malformed JSON must not raise — the consumer loop must survive."""
    maker = _FakeSessionMaker()
    redis = _FakeRedis()

    await handle_heatmap(maker, b"not-json-at-all", redis=redis, channel=CHANNEL)

    # Nothing inserted, nothing published
    assert maker.session.added == []
    assert redis.publishes == []


@pytest.mark.asyncio
async def test_handle_heatmap_skips_missing_cells_key() -> None:
    """A payload missing the 'cells' key must be skipped without raising."""
    maker = _FakeSessionMaker()
    redis = _FakeRedis()
    raw = json.dumps({"camera_id": "cam_01", "ts": TS_STR}).encode()

    await handle_heatmap(maker, raw, redis=redis, channel=CHANNEL)

    assert maker.session.added == []
    assert redis.publishes == []


@pytest.mark.asyncio
async def test_handle_heatmap_redis_failure_does_not_raise() -> None:
    """A Redis publish error must not propagate — best-effort only."""

    class _FailRedis:
        async def publish(self, channel: str, message: bytes | str) -> None:
            raise OSError("redis down")

    maker = _FakeSessionMaker()
    raw = json.dumps(GOOD_PAYLOAD).encode()

    # Must complete without raising even when Redis is unavailable.
    await handle_heatmap(maker, raw, redis=_FailRedis(), channel=CHANNEL)
    assert maker.session.committed


# ---------------------------------------------------------------------------
# Migration test: idx_density_latest exists after upgrade
# ---------------------------------------------------------------------------


def test_density_index_exists_after_migration() -> None:
    """Migration 0004 creates idx_density_latest on density_snapshots."""
    from pathlib import Path

    import sqlalchemy as sa
    from alembic import command
    from alembic.config import Config
    from testcontainers.postgres import PostgresContainer

    repo_root = Path(__file__).resolve().parents[2]
    migrations = repo_root / "cloud" / "migrations"

    with PostgresContainer("postgres:16") as pg:
        url = pg.get_connection_url()
        cfg = Config(str(migrations / "alembic.ini"))
        cfg.set_main_option("script_location", str(migrations))
        cfg.set_main_option("sqlalchemy.url", url)
        command.upgrade(cfg, "head")

        engine = sa.create_engine(url)
        insp = sa.inspect(engine)
        idx_names = {ix["name"] for ix in insp.get_indexes("density_snapshots")}
        assert "idx_density_latest" in idx_names
        engine.dispose()
