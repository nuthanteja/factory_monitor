"""Integration tests for GET /api/v1/heatmap and GET /api/v1/zones.

Uses testcontainers Postgres 16 (mirror of test_api_cameras.py).
Seeds density_snapshots with multiple timestamps per (camera, zone) plus an
out-of-window row; asserts that only the latest per zone is returned and the old
row is excluded.  Also covers the malformed window 422 case and the zones endpoint.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from cloud.api.deps import get_session_maker
from cloud.api.main import create_app
from cloud.common.config import Settings

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
async def session_maker(migrated_url: str):
    engine = create_async_engine(migrated_url, future=True)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


@pytest_asyncio.fixture
async def client(session_maker):
    app = create_app(
        Settings(
            ws_fanout_enabled=False,
            seed_cameras_enabled=False,
            heatmap_ws_enabled=False,
        )
    )
    app.dependency_overrides[get_session_maker] = lambda: session_maker
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ---------------------------------------------------------------------------
# Helper: seed data
# ---------------------------------------------------------------------------

NOW = datetime.now(tz=UTC)
WITHIN = NOW - timedelta(minutes=5)         # inside 15m window
OLDER = NOW - timedelta(minutes=8)          # also inside window — earlier snapshot
OLD_ROW = NOW - timedelta(minutes=20)       # outside 15m window


async def _seed_heatmap(session_maker: async_sessionmaker) -> None:
    """Insert density_snapshot rows:
    - cam_01/zone_a: two rows within window (latest = WITHIN, count=7)
    - cam_01/zone_b: one row within window (count=2)
    - cam_02/zone_c: one row within window (count=5)
    - cam_01/zone_a: one old row outside window (count=99) — must be excluded
    """
    async with session_maker() as session:
        await session.execute(text("DELETE FROM density_snapshots"))
        await session.execute(
            text(
                "INSERT INTO density_snapshots (camera_id, zone_id, count, ts) VALUES"
                " (:c1, :z1, 3, :older),"   # cam_01/zone_a older — should NOT be in result
                " (:c1, :z1, 7, :within),"  # cam_01/zone_a latest — SHOULD appear
                " (:c1, :z2, 2, :within),"  # cam_01/zone_b
                " (:c2, :z3, 5, :within),"  # cam_02/zone_c
                " (:c1, :z1, 99, :old)"     # cam_01/zone_a outside window — excluded
            ),
            {
                "c1": "cam_01",
                "c2": "cam_02",
                "z1": "zone_a",
                "z2": "zone_b",
                "z3": "zone_c",
                "within": WITHIN,
                "older": OLDER,
                "old": OLD_ROW,
            },
        )
        await session.commit()


async def _seed_zones(session_maker: async_sessionmaker) -> None:
    """Insert a couple of zones with polygon data."""
    async with session_maker() as session:
        await session.execute(text("DELETE FROM zones"))
        await session.execute(
            text(
                "INSERT INTO zones (id, site_id, camera_id, name, kind, polygon)"
                " VALUES"
                " ('zone_a', 'site_01', 'cam_01', 'Zone A', 'restricted',"
                "  '[[0,0],[100,0],[100,100],[0,100]]'::jsonb),"
                " ('zone_b', 'site_01', 'cam_01', 'Zone B', 'restricted',"
                "  '[[200,0],[300,0],[300,100],[200,100]]'::jsonb)"
            )
        )
        await session.commit()


# ---------------------------------------------------------------------------
# Tests: GET /api/v1/heatmap
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_heatmap_returns_latest_per_zone(client, session_maker):
    """Latest snapshot per (camera_id, zone_id) is returned; old rows excluded."""
    await _seed_heatmap(session_maker)

    resp = await client.get("/api/v1/heatmap?window=15m")
    assert resp.status_code == 200
    body = resp.json()

    assert "cameras" in body
    assert "meta" in body
    assert body["meta"]["window"] == "15m"
    assert "server_now" in body["meta"]

    # Build a lookup: {camera_id: {zone_id: count}}
    by_cam = {cam["camera_id"]: cam for cam in body["cameras"]}

    # cam_01 must have zone_a (count=7, not 3 or 99) and zone_b (count=2)
    assert "cam_01" in by_cam
    cam01_cells = {cell["zone_id"]: cell for cell in by_cam["cam_01"]["cells"]}
    assert "zone_a" in cam01_cells
    # must be the latest (7), not the older row (3) or the out-of-window row (99)
    assert cam01_cells["zone_a"]["count"] == 7
    assert "zone_b" in cam01_cells
    assert cam01_cells["zone_b"]["count"] == 2

    # cam_02 must have zone_c
    assert "cam_02" in by_cam
    cam02_cells = {cell["zone_id"]: cell for cell in by_cam["cam_02"]["cells"]}
    assert "zone_c" in cam02_cells
    assert cam02_cells["zone_c"]["count"] == 5

    # Each cell must have zone_id, count, ts keys
    for cam in body["cameras"]:
        for cell in cam["cells"]:
            assert set(cell.keys()) == {"zone_id", "count", "ts"}


@pytest.mark.asyncio
async def test_heatmap_excludes_out_of_window_rows(client, session_maker):
    """The out-of-window row (20m ago, count=99) must NOT appear."""
    await _seed_heatmap(session_maker)

    resp = await client.get("/api/v1/heatmap?window=15m")
    assert resp.status_code == 200
    body = resp.json()

    for cam in body["cameras"]:
        for cell in cam["cells"]:
            assert cell["count"] != 99, "out-of-window row (count=99) must be excluded"


@pytest.mark.asyncio
async def test_heatmap_malformed_window_returns_422(client, session_maker):
    """A non-parseable window string must return 422."""
    resp = await client.get("/api/v1/heatmap?window=abc")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_heatmap_empty_table_returns_empty_cameras(client, session_maker):
    """An empty density_snapshots table returns cameras=[]."""
    async with session_maker() as session:
        await session.execute(text("DELETE FROM density_snapshots"))
        await session.commit()

    resp = await client.get("/api/v1/heatmap")
    assert resp.status_code == 200
    assert resp.json()["cameras"] == []


# ---------------------------------------------------------------------------
# Tests: GET /api/v1/zones
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_zones_returns_seeded_zones(client, session_maker):
    """GET /api/v1/zones returns all seeded zones with polygon data."""
    await _seed_zones(session_maker)

    resp = await client.get("/api/v1/zones")
    assert resp.status_code == 200
    body = resp.json()

    assert "zones" in body
    zones = body["zones"]
    assert len(zones) == 2  # noqa: PLR2004

    ids = [z["id"] for z in zones]
    assert ids == sorted(ids), "zones must be ordered by id"

    by_id = {z["id"]: z for z in zones}
    assert "zone_a" in by_id
    assert by_id["zone_a"]["camera_id"] == "cam_01"
    assert by_id["zone_a"]["name"] == "Zone A"
    # polygon must pass through as list of [x,y]
    polygon_a = by_id["zone_a"]["polygon"]
    assert isinstance(polygon_a, list)
    assert len(polygon_a) == 4  # noqa: PLR2004

    # Key set check
    for z in zones:
        assert set(z.keys()) == {"id", "camera_id", "name", "polygon"}
