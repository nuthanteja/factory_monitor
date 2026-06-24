"""Lazy per-process redis.asyncio client keyed off Settings.redis_url.

Used by the writers (best-effort publish) and the WS subscriber. The client
is created on first use and reused; a connection failure surfaces only when
publish/subscribe is attempted, where it is handled best-effort.
"""
from __future__ import annotations

import redis.asyncio as aioredis

from cloud.common.config import Settings

_client: aioredis.Redis | None = None


def get_redis(settings: Settings) -> aioredis.Redis:
    """Return the cached asyncio Redis client (created on first call)."""
    global _client
    if _client is None:
        _client = aioredis.from_url(settings.redis_url, decode_responses=False)
    return _client


async def close_redis() -> None:
    """Close + drop the cached client (call on app/worker shutdown)."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
