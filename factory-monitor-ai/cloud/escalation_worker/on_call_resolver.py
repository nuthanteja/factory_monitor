"""OnCallResolver — class wrapper around cloud.common.on_call_resolver.resolve.

The underlying implementation is a bare async function in cloud.common.on_call_resolver.
This module provides the class interface declared in the Task-18 contract so that the
e2e test suite can construct an OnCallResolver(session_maker) object and call .resolve().
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import async_sessionmaker

from cloud.common.db.models import User
from cloud.common.on_call_resolver import resolve as _resolve

if TYPE_CHECKING:
    pass


class OnCallResolver:
    """Resolve who is on-call for a given role/zone/site at a point in time.

    Falls back to plant-wide (zone_id=NULL) assignment if no zone-specific match.
    Raises LookupError if no assignment is found.
    """

    def __init__(self, session_maker: async_sessionmaker) -> None:
        self._session_maker = session_maker

    async def resolve(
        self,
        role: str,
        zone_id: str | None,
        at: datetime | None = None,
        *,
        site_id: str = "plant-01",
    ) -> User:
        """Return the on-call User for role+zone_id at time `at`.

        Falls back to zone_id=NULL plant-wide assignment when no zone-specific
        match is found.  Raises LookupError when no assignment covers the window.
        """
        async with self._session_maker() as session:
            user = await _resolve(
                session,
                role=role,
                site_id=site_id,
                zone_id=zone_id,
                at=at,
            )
        if user is None:
            raise LookupError(
                f"No on-call assignment for role={role!r} zone_id={zone_id!r} site_id={site_id!r} at={at}"
            )
        return user
