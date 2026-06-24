"""Shared escalation helpers (tier config lookup).

Both the ingest worker (tier-0 path) and the escalation transition worker
use ``fetch_tier_config`` to look up an EscalationTier row.  Keeping one
canonical copy here prevents the two code paths from drifting.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cloud.common.db.models import EscalationTier


async def fetch_tier_config(
    session: AsyncSession,
    site_id: str,
    anomaly_type: str,
    tier: int,
) -> EscalationTier | None:
    """Return tier config for (site, anomaly_type, tier), falling back to site-wide (NULL).

    Filters to rows where anomaly_type matches exactly OR is NULL, then
    prefers the specific row over the NULL fallback (ORDER BY … IS NULL ASC).
    A row for a *different* anomaly_type is never returned.
    """
    stmt = (
        select(EscalationTier)
        .where(EscalationTier.site_id == site_id)
        .where(EscalationTier.tier == tier)
        .where(
            (EscalationTier.anomaly_type == anomaly_type)
            | (EscalationTier.anomaly_type.is_(None))
        )
        .order_by(EscalationTier.anomaly_type.is_(None).asc())  # specific before NULL
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()
