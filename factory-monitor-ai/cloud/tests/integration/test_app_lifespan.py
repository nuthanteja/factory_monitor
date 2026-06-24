"""Integration test for the FastAPI app lifespan introduced in Task 25.

Verifies that:
- ws_fanout_enabled=True (default): startup sets app.state.ws_redis and
  app.state.ws_fanout (the supervisor asyncio.Task), and the app shuts down
  cleanly without leaking tasks.
- ws_fanout_enabled=False: startup skips Redis+fanout entirely (no
  app.state.ws_redis, no app.state.ws_fanout).

Redis may or may not be reachable — the supervisor handles that transparently
via its Postgres-poll fallback, so the test is resilient to Redis being absent.
A testcontainers Postgres is used to satisfy the session_maker requirement.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
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
def migrated_async_url(pg_container: PostgresContainer) -> str:
    sync_url = pg_container.get_connection_url()
    cfg = Config()
    cfg.set_main_option("script_location", MIGRATIONS)
    cfg.set_main_option("sqlalchemy.url", sync_url)
    command.upgrade(cfg, "head")
    return _async_url(sync_url)


@pytest.fixture
def maker(migrated_async_url: str):
    engine = create_async_engine(migrated_async_url, future=True)
    return async_sessionmaker(engine, expire_on_commit=False)


# ── lifespan ENABLED ─────────────────────────────────────────────────────────


def test_lifespan_enabled_sets_ws_redis_and_ws_fanout(maker):
    """With ws_fanout_enabled=True, startup wires app.state.ws_redis and
    creates the supervisor task (app.state.ws_fanout).

    Redis is not required to be up — the supervisor falls back to the
    Postgres-poll path transparently, so the test is always deterministic.
    """
    settings = Settings(ws_fanout_enabled=True)
    app = create_app(settings)
    app.dependency_overrides[get_session_maker] = lambda: maker
    app.state.ws_session_maker = maker

    with TestClient(app) as _client:
        # After startup the two state attributes must be present.
        assert hasattr(app.state, "ws_redis"), (
            "lifespan must set app.state.ws_redis on startup"
        )
        assert hasattr(app.state, "ws_fanout"), (
            "lifespan must set app.state.ws_fanout (supervisor task) on startup"
        )
        task = app.state.ws_fanout
        assert isinstance(task, asyncio.Task), "ws_fanout must be an asyncio.Task"
        assert not task.done(), "supervisor task must still be running during TestClient body"

    # After TestClient.__exit__ the lifespan shutdown has run.
    assert task.done(), "supervisor task must be done after shutdown"


# ── lifespan DISABLED ────────────────────────────────────────────────────────


def test_lifespan_disabled_skips_redis_and_fanout(maker):
    """With ws_fanout_enabled=False, startup does NOT set ws_redis or ws_fanout,
    and no Redis connection is attempted — safe for pure-unit / no-Redis tests.
    """
    settings = Settings(ws_fanout_enabled=False)
    app = create_app(settings)
    app.dependency_overrides[get_session_maker] = lambda: maker
    app.state.ws_session_maker = maker

    with TestClient(app) as _client:
        assert not hasattr(app.state, "ws_redis"), (
            "ws_fanout_enabled=False must NOT set app.state.ws_redis"
        )
        assert not hasattr(app.state, "ws_fanout"), (
            "ws_fanout_enabled=False must NOT set app.state.ws_fanout"
        )
    # Clean shutdown with nothing to tear down — no assertion needed, just no error.
