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


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    maker = session_factory(get_settings())
    async with maker() as session:
        yield session
