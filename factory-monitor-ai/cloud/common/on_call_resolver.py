"""On-call resolver — §3.2 / §6.

Resolves who holds a given escalation role right now from the roster tables.
Zone-specific assignment wins over plant-wide (zone_id IS NULL) fallback.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from cloud.common.db.models import OnCallAssignment, User


async def resolve(
    session: AsyncSession,
    role: str,
    site_id: str,
    zone_id: str | None,
    at: datetime | None = None,
) -> User | None:
    """Return the on-call User for `role` at time `at` (defaults to now()).

    Priority: zone-specific assignment (matching zone_id) beats plant-wide
    (zone_id IS NULL). Returns None when no assignment covers the window.
    """
    if at is None:
        at = datetime.now(UTC)

    # Build a subquery that finds assignments active at `at` for this role+site,
    # preferring zone-specific rows (zone_id = :zone_id) over plant-wide (IS NULL).
    # We order so zone-specific rows come first, then take the first match.
    zone_match_expr = (
        OnCallAssignment.zone_id == zone_id
        if zone_id is not None
        else OnCallAssignment.zone_id.is_(None)
    )

    stmt = (
        select(User)
        .join(OnCallAssignment, OnCallAssignment.user_id == User.id)
        .where(
            and_(
                OnCallAssignment.site_id == site_id,
                OnCallAssignment.role == role,
                OnCallAssignment.starts_at <= at,
                OnCallAssignment.ends_at > at,
                or_(
                    zone_id is not None and zone_match_expr,
                    OnCallAssignment.zone_id.is_(None),
                )
                if zone_id is not None
                else OnCallAssignment.zone_id.is_(None),
            )
        )
        .order_by(
            # zone_id IS NOT NULL sorts before NULL — zone-specific wins
            OnCallAssignment.zone_id.is_(None).asc(),
            # among ties pick the most recent assignment
            OnCallAssignment.starts_at.desc(),
        )
        .limit(1)
    )

    result = await session.execute(stmt)
    return result.scalar_one_or_none()
