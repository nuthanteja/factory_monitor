"""Async engine + session factory keyed off Settings."""
from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from cloud.common.config import Settings, get_settings


def session_factory(settings: Settings) -> async_sessionmaker[AsyncSession]:
    """Build an async_sessionmaker bound to a fresh engine for `settings`."""
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )


_maker: async_sessionmaker[AsyncSession] | None = None


def get_maker() -> async_sessionmaker[AsyncSession]:
    """Return the module-level cached session maker, creating it on first call."""
    global _maker
    if _maker is None:
        _maker = session_factory(get_settings())
    return _maker


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield a single AsyncSession from the cached session maker.

    The caller is responsible for commit()/rollback(); the session does not
    auto-commit.
    """
    async with get_maker()() as session:
        yield session
