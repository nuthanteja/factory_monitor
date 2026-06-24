"""Demo roster + escalation tier seed for development and integration tests.

Inserts use ON CONFLICT DO NOTHING (users, assignments) or DO UPDATE (tiers)
so re-running is safe/idempotent.
Tier 0 = OPERATOR (grace period before first escalation).
Tier 1 = FLOOR_MANAGER.
Tier 2 = PLANT_DIRECTOR (last escalation before CRITICAL_UNRESOLVED).
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

_DEMO_USERS = [
    {
        "id": uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001"),
        "site_id": "plant-01",
        "full_name": "Demo Operator",
        "phone_e164": "+15550000001",
        "role": "OPERATOR",
        "is_active": True,
    },
    {
        "id": uuid.UUID("aaaaaaaa-0000-0000-0000-000000000002"),
        "site_id": "plant-01",
        "full_name": "Demo Floor Manager",
        "phone_e164": "+15550000002",
        "role": "FLOOR_MANAGER",
        "is_active": True,
    },
    {
        "id": uuid.UUID("aaaaaaaa-0000-0000-0000-000000000003"),
        "site_id": "plant-01",
        "full_name": "Demo Plant Director",
        "phone_e164": "+15550000003",
        "role": "PLANT_DIRECTOR",
        "is_active": True,
    },
]

_ROLE_USER_MAP = {
    "OPERATOR": uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001"),
    "FLOOR_MANAGER": uuid.UUID("aaaaaaaa-0000-0000-0000-000000000002"),
    "PLANT_DIRECTOR": uuid.UUID("aaaaaaaa-0000-0000-0000-000000000003"),
}

_DEMO_TIERS = [
    {"tier": 0, "role": "OPERATOR", "template_name": "operator_alert_v1"},
    {"tier": 1, "role": "FLOOR_MANAGER", "template_name": "floor_manager_alert_v1"},
    {"tier": 2, "role": "PLANT_DIRECTOR", "template_name": "plant_director_alert_v1"},
]


async def seed_demo_roster(session_maker: async_sessionmaker) -> None:
    """Insert 3 demo users + plant-wide on-call assignments (30-day window)."""
    now = datetime.now(UTC)
    starts_at = now - timedelta(days=1)
    ends_at = now + timedelta(days=30)

    async with session_maker() as s:
        for u in _DEMO_USERS:
            await s.execute(
                text(
                    "INSERT INTO users (id, site_id, full_name, phone_e164, role, is_active)"
                    " VALUES (:id, :site_id, :full_name, :phone_e164, :role, :is_active)"
                    " ON CONFLICT (id) DO NOTHING"
                ),
                u,
            )

        for role, user_id in _ROLE_USER_MAP.items():
            assign_id = uuid.uuid5(uuid.NAMESPACE_DNS, f"demo-assign-{role}")
            await s.execute(
                text(
                    "INSERT INTO on_call_assignments"
                    " (id, site_id, role, zone_id, user_id, starts_at, ends_at)"
                    " VALUES (:id, :site_id, :role, NULL, :user_id, :starts_at, :ends_at)"
                    " ON CONFLICT (id) DO NOTHING"
                ),
                {
                    "id": assign_id,
                    "site_id": "plant-01",
                    "role": role,
                    "user_id": user_id,
                    "starts_at": starts_at,
                    "ends_at": ends_at,
                },
            )
        await s.commit()


async def seed_demo_tiers(
    session_maker: async_sessionmaker,
    site_id: str = "plant-01",
    delay_seconds: int = 5,
) -> None:
    """Insert escalation tier config rows (site-wide, anomaly_type=NULL).

    delay_seconds is intentionally short for demo/test; production values
    come from escalation_tiers.delay_seconds rows (tier1≈300s, tier2≈900s).
    """
    async with session_maker() as s:
        for t in _DEMO_TIERS:
            tier_id = uuid.uuid5(
                uuid.NAMESPACE_DNS, f"demo-tier-{site_id}-{t['tier']}"
            )
            await s.execute(
                text(
                    "INSERT INTO escalation_tiers"
                    " (id, site_id, anomaly_type, tier, role, delay_seconds, template_name)"
                    " VALUES (:id, :site_id, NULL, :tier, :role, :delay_seconds, :template_name)"
                    " ON CONFLICT (id) DO UPDATE"
                    "   SET delay_seconds = EXCLUDED.delay_seconds,"
                    "       template_name = EXCLUDED.template_name"
                ),
                {
                    "id": tier_id,
                    "site_id": site_id,
                    "tier": t["tier"],
                    "role": t["role"],
                    "delay_seconds": delay_seconds,
                    "template_name": t["template_name"],
                },
            )
        await s.commit()
