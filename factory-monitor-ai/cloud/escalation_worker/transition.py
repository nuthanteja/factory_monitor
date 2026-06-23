"""Escalation state-machine transition — §6 design spec.

fire_transition(session, incident) executes ONE tier advancement inside the
caller's already-open transaction.  The caller is responsible for commit().

Exactly-once guarantee:
  INSERT INTO escalation_idempotency (incident_id, tier) ON CONFLICT DO NOTHING
  → if 0 rows inserted the tier was already fired; skip all side effects.

Side effects (all in the same txn):
  1. INSERT escalation_idempotency (incident_id, new_tier) — idempotency guard
  2. Resolve next on-call recipient via on_call_resolver
  3. INSERT incident_events (TIER{n}_FIRED | CRITICAL)
  4. UPDATE incidents (status, current_tier, next_fire_at, deadline_at)
  5. INSERT outbox (kind=TEMPLATE, idempotency_key=incident_id|tier)

State machine (§6 transition table):
  AWAITING_OPERATOR (tier 0) → TIER1   idemp(inc,1)  FLOOR_MANAGER
  TIER1             (tier 1) → TIER2   idemp(inc,2)  PLANT_DIRECTOR
  TIER2             (tier 2) → CRITICAL_UNRESOLVED  idemp(inc,3)  next_fire_at=NULL
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from cloud.common.db.models import (
    EscalationIdempotency,
    EscalationTier,
    Incident,
    IncidentEvent,
    IncidentStatus,
    Outbox,
)
from cloud.common.escalation import fetch_tier_config
from cloud.common.on_call_resolver import resolve


@dataclass(frozen=True)
class TransitionResult:
    fired: bool
    skipped_idempotent: bool
    new_status: str | None


# Maps current (status, tier) → (next_status, next_tier, event_type, next_role_or_None)
_TRANSITION_TABLE: dict[
    tuple[str, int], tuple[str, int, str, str | None]
] = {
    (IncidentStatus.AWAITING_OPERATOR.value, 0): (
        IncidentStatus.TIER1.value, 1, "TIER1_FIRED", "FLOOR_MANAGER",
    ),
    (IncidentStatus.TIER1.value, 1): (
        IncidentStatus.TIER2.value, 2, "TIER2_FIRED", "PLANT_DIRECTOR",
    ),
    (IncidentStatus.TIER2.value, 2): (
        IncidentStatus.CRITICAL_UNRESOLVED.value, 3, "CRITICAL", None,
    ),
}


async def _idempotency_insert(
    session: AsyncSession, incident_id: uuid.UUID, tier: int
) -> bool:
    """INSERT ON CONFLICT DO NOTHING; returns True iff the row was inserted (first fire)."""
    stmt = (
        pg_insert(EscalationIdempotency)
        .values(incident_id=incident_id, tier=tier)
        .on_conflict_do_nothing(index_elements=["incident_id", "tier"])
    )
    result = await session.execute(stmt)
    return result.rowcount > 0



async def fire_transition(
    session: AsyncSession,
    incident: Incident,
) -> TransitionResult:
    """Advance `incident` by one tier in the caller's transaction.

    Reads the escalation_tiers config from the DB; uses on_call_resolver to
    pick the next recipient.  If the idempotency guard detects a duplicate
    (already fired) it returns TransitionResult(fired=False, skipped_idempotent=True).
    """
    key = (incident.status.value, incident.current_tier)
    if key not in _TRANSITION_TABLE:
        # Terminal or unknown state — nothing to do
        return TransitionResult(fired=False, skipped_idempotent=False, new_status=None)

    next_status, next_tier, event_type, next_role = _TRANSITION_TABLE[key]

    # 1. Idempotency guard — exactly-once effect
    inserted = await _idempotency_insert(session, incident.id, next_tier)
    if not inserted:
        # Another worker already committed this tier; skip all side effects
        return TransitionResult(fired=False, skipped_idempotent=True, new_status=None)

    now = datetime.now(timezone.utc)

    # 2. Fetch tier config once (NULL for terminal tier; not needed)
    tier_cfg: EscalationTier | None = None
    if next_status != IncidentStatus.CRITICAL_UNRESOLVED.value:
        tier_cfg = await fetch_tier_config(
            session, incident.site_id, incident.anomaly_type, next_tier
        )

    # Compute next_fire_at from cached config (or NULL for terminal)
    new_next_fire_at: datetime | None = None
    if tier_cfg is not None:
        new_next_fire_at = now + timedelta(seconds=tier_cfg.delay_seconds)

    # 3. Resolve next on-call recipient
    outbox_phone: str | None = None
    outbox_template: str | None = None
    if next_role is not None:
        recipient = await resolve(
            session, role=next_role, site_id=incident.site_id,
            zone_id=incident.zone_id, at=now
        )
        if recipient is not None:
            outbox_phone = recipient.phone_e164
        # reuse cached tier_cfg for template (no second DB round-trip)
        if tier_cfg is not None:
            outbox_template = tier_cfg.template_name

    # 4. Audit event
    audit = IncidentEvent(
        incident_id=incident.id,
        type=event_type,
        from_state=incident.status.value,
        to_state=next_status,
        tier=next_tier,
        payload={
            "previous_tier": incident.current_tier,
            "new_tier": next_tier,
            "recipient_phone": outbox_phone,
        },
    )
    session.add(audit)

    # 5. UPDATE incidents row
    await session.execute(
        text(
            "UPDATE incidents SET"
            "  status = :status,"
            "  current_tier = :tier,"
            "  next_fire_at = :nfa,"
            "  deadline_at = :nfa,"
            "  claimed_by = NULL,"
            "  claimed_until = NULL,"
            "  updated_at = now()"
            " WHERE id = :id"
        ),
        {
            "status": next_status,
            "tier": next_tier,
            "nfa": new_next_fire_at,
            "id": incident.id,
        },
    )

    # 6. Outbox row (only when there is a real next recipient)
    if outbox_phone is not None and outbox_template is not None:
        outbox_row = Outbox(
            id=uuid.uuid4(),
            incident_id=incident.id,
            tier=next_tier,
            to_phone_e164=outbox_phone,
            channel="console",
            kind="TEMPLATE",
            template_name=outbox_template,
            idempotency_key=f"{incident.id}|{next_tier}",
            status="PENDING",
            attempts=0,
            max_attempts=6,
        )
        session.add(outbox_row)

    await session.flush()

    return TransitionResult(fired=True, skipped_idempotent=False, new_status=next_status)
