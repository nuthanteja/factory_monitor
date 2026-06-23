"""Acknowledge service — sets incident to ACK status and clears escalation timer.

Used by the test suite (test_phase2a_e2e) to exercise the ACK path without
going through the HTTP API layer.  The logic mirrors cloud.api.routes.acknowledge_incident.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from cloud.common.db.models import Incident, IncidentEvent, IncidentStatus

_ACKABLE_FROM = {
    IncidentStatus.AWAITING_OPERATOR,
    IncidentStatus.TIER1,
    IncidentStatus.TIER2,
}


async def acknowledge_incident(
    session_maker: async_sessionmaker,
    incident_id: uuid.UUID,
    *,
    actor_user_id: uuid.UUID,
) -> None:
    """Set status=ACK, next_fire_at=NULL, insert incident_events(ACK).

    Idempotent: if the incident is already ACK or RESOLVED, this is a no-op.
    """
    async with session_maker() as session:
        inc = (
            await session.execute(
                select(Incident).where(Incident.id == incident_id)
            )
        ).scalar_one_or_none()

        if inc is None:
            raise LookupError(f"Incident {incident_id} not found")

        # Already in a terminal/acked state — idempotent no-op
        if inc.status in (IncidentStatus.ACK, IncidentStatus.RESOLVED, IncidentStatus.CRITICAL_UNRESOLVED):
            return

        if inc.status not in _ACKABLE_FROM:
            raise ValueError(
                f"Cannot acknowledge incident in status {inc.status.value}"
            )

        now = datetime.now(tz=timezone.utc)
        prev_status = inc.status.value
        inc.status = IncidentStatus.ACK
        inc.next_fire_at = None
        inc.deadline_at = None
        inc.acked_at = now
        inc.acked_by = actor_user_id
        inc.updated_at = now

        session.add(
            IncidentEvent(
                incident_id=inc.id,
                type="ACK",
                from_state=prev_status,
                to_state="ACK",
                tier=inc.current_tier,
                actor_user_id=actor_user_id,
                payload={"actor_user_id": str(actor_user_id)},
            )
        )
        await session.commit()
