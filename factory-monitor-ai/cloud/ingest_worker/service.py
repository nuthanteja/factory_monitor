from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from cloud.common.db.models import Incident, IncidentEvent, IncidentStatus
from cloud.common.schemas.anomaly import AnomalyEvent

_OPEN_STATUSES = (
    IncidentStatus.AWAITING_OPERATOR,
    IncidentStatus.TIER1,
    IncidentStatus.TIER2,
)


@dataclass(frozen=True)
class IncidentResult:
    incident_id: uuid.UUID | None
    created: bool
    reason: str  # "created" | "duplicate_event_id" | "duplicate_open_dedup"


async def _open_incident_exists(session: AsyncSession, dedup_key: str) -> bool:
    stmt = (
        select(Incident.id)
        .where(Incident.dedup_key == dedup_key)
        .where(Incident.status.in_(_OPEN_STATUSES))
        .limit(1)
    )
    return (await session.execute(stmt)).first() is not None


async def _event_id_seen(session: AsyncSession, event_id: uuid.UUID) -> bool:
    stmt = select(IncidentEvent.id).where(IncidentEvent.source_event_id == event_id).limit(1)
    return (await session.execute(stmt)).first() is not None


async def create_incident_from_anomaly(
    session: AsyncSession,
    event: AnomalyEvent,
    *,
    grace_seconds: int,
) -> IncidentResult:
    """Create one incident + one CREATED audit row in a single flush sequence.

    Idempotent on event.event_id (UNIQUE incident_events.source_event_id) and
    deduplicated on an open incident sharing event.dedup_key
    (partial UNIQUE uq_incident_open_dedup). The caller owns commit/rollback.
    """
    source_event_id = uuid.UUID(str(event.event_id))

    if await _event_id_seen(session, source_event_id):
        return IncidentResult(incident_id=None, created=False, reason="duplicate_event_id")

    if await _open_incident_exists(session, event.dedup_key):
        return IncidentResult(incident_id=None, created=False, reason="duplicate_open_dedup")

    now = datetime.now(tz=timezone.utc)
    snapshot_url = event.evidence.snapshot_url or ""

    incident = Incident(
        id=uuid.uuid4(),
        site_id=event.site_id,
        camera_id=event.camera_id,
        zone_id=event.zone_id,
        anomaly_type=event.anomaly_type.value,
        rule_id=event.rule_id,
        object_class=event.object_class,
        track_id=event.track_id,
        severity=event.severity.value,
        dedup_key=event.dedup_key,
        status=IncidentStatus.AWAITING_OPERATOR,
        current_tier=0,
        next_fire_at=now + timedelta(seconds=grace_seconds),
        snapshot_url=snapshot_url,
        is_synthetic=False,
    )
    session.add(incident)

    try:
        await session.flush()  # populate incident.id; may raise on dedup_key unique violation
        audit = IncidentEvent(
            incident_id=incident.id,
            type="CREATED",
            from_state=None,
            to_state=IncidentStatus.AWAITING_OPERATOR.value,
            tier=0,
            source_event_id=source_event_id,
            payload={
                "event_id": str(event.event_id),
                "anomaly_type": event.anomaly_type.value,
                "rule_id": event.rule_id,
                "confidence": event.confidence,
                "occurred_at": event.occurred_at.isoformat(),
                "source": event.source,
            },
        )
        session.add(audit)
        await session.flush()  # may raise on source_event_id unique violation
    except IntegrityError:
        await session.rollback()
        if await _event_id_seen(session, source_event_id):
            return IncidentResult(incident_id=None, created=False, reason="duplicate_event_id")
        if await _open_incident_exists(session, event.dedup_key):
            return IncidentResult(incident_id=None, created=False, reason="duplicate_open_dedup")
        raise

    return IncidentResult(incident_id=incident.id, created=True, reason="created")
