from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from cloud.api.deps import get_session_maker
from cloud.api.main import create_app
from cloud.common.config import Settings
from cloud.common.seed_cameras import _DEMO_CAMERAS, seed_cameras

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
    app = create_app(Settings(ws_fanout_enabled=False, seed_cameras_enabled=False))
    app.dependency_overrides[get_session_maker] = lambda: session_maker
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_list_cameras_returns_seeded_cameras(client, session_maker):
    await seed_cameras(session_maker)

    resp = await client.get("/api/v1/cameras")
    assert resp.status_code == 200
    body = resp.json()

    assert "cameras" in body
    cameras = body["cameras"]
    assert len(cameras) == len(_DEMO_CAMERAS)

    # ids must be sorted
    ids = [c["id"] for c in cameras]
    assert ids == sorted(ids)

    # first camera whep_url must be relative same-origin
    cam_01 = next(c for c in cameras if c["id"] == "cam_01")
    assert cam_01["whep_url"] == "/whep/cam_01/whep"

    # exact key set per camera (guards accidental additions/renames)
    for cam in cameras:
        assert set(cam.keys()) == {"id", "name", "whep_url", "zone_id", "rtsp_path"}


@pytest.mark.asyncio
async def test_seed_cameras_idempotent(session_maker):
    """Calling seed_cameras twice must not raise and must not duplicate rows."""
    await seed_cameras(session_maker)
    await seed_cameras(session_maker)

    from sqlalchemy import text

    async with session_maker() as s:
        result = await s.execute(text("SELECT COUNT(*) FROM cameras"))
        count = result.scalar_one()

    assert count == len(_DEMO_CAMERAS)
