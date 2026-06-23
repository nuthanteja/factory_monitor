from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from cloud.common.db.models import (
    Incident,
    IncidentEvent,
    IncidentStatus,
    Outbox,
    User,
)
from cloud.common.escalation import fetch_tier_config
from cloud.common.schemas.anomaly import AnomalyEvent

_OPEN_STATUSES = (
    IncidentStatus.AWAITING_OPERATOR,
    IncidentStatus.TIER1,
    IncidentStatus.TIER2,
)

# Type alias for the on-call resolver callable.
# Matches resolve(session, role, site_id, zone_id, at) -> User | None
OnCallResolverFn = Callable[
    [AsyncSession, str, str, str | None, datetime],
    Coroutine[Any, Any, User | None],
]


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


async def _enqueue_outbox(
    session: AsyncSession,
    incident: Incident,
    tier: int,
    to_phone_e164: str,
    template_name: str,
) -> None:
    """Insert a PENDING outbox row atomically in the caller's transaction."""
    outbox_row = Outbox(
        id=uuid.uuid4(),
        incident_id=incident.id,
        tier=tier,
        to_phone_e164=to_phone_e164,
        channel="console",  # ConsoleProvider default; notifier upgrades per NOTIFY_PROVIDER_CHAIN
        kind="TEMPLATE",
        template_name=template_name,
        idempotency_key=f"{incident.id}|{tier}",
        status="PENDING",
        attempts=0,
        max_attempts=6,
    )
    session.add(outbox_row)
    await session.flush()


async def create_incident_from_anomaly(
    session: AsyncSession,
    event: AnomalyEvent,
    *,
    grace_seconds: int,
    on_call_resolver: OnCallResolverFn | None = None,
) -> IncidentResult:
    """Create one incident + CREATED audit row + optional tier-0 outbox row in one txn.

    Idempotent on event.event_id (UNIQUE incident_events.source_event_id) and
    deduplicated on an open incident sharing event.dedup_key
    (partial UNIQUE uq_incident_open_dedup). The caller owns commit/rollback.

    When on_call_resolver is provided, the tier-0 OPERATOR outbox row is inserted
    atomically in the same transaction (§3.2 / §6 design spec).
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
        deadline_at=now + timedelta(seconds=grace_seconds),
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

        # Enqueue tier-0 OPERATOR outbox row atomically when a resolver is wired in.
        if on_call_resolver is not None:
            tier_cfg = await fetch_tier_config(
                session, event.site_id, event.anomaly_type.value, tier=0
            )
            operator = await on_call_resolver(
                session, "OPERATOR", event.site_id, event.zone_id, now
            )
            if operator is not None and tier_cfg is not None:
                await _enqueue_outbox(
                    session,
                    incident,
                    tier=0,
                    to_phone_e164=operator.phone_e164,
                    template_name=tier_cfg.template_name,
                )
            else:
                logger.warning(
                    "tier-0 operator outbox skipped: incident=%s operator_found=%s tier0_config_found=%s",
                    incident.id,
                    operator is not None,
                    tier_cfg is not None,
                )

    except IntegrityError:
        await session.rollback()
        if await _event_id_seen(session, source_event_id):
            return IncidentResult(incident_id=None, created=False, reason="duplicate_event_id")
        if await _open_incident_exists(session, event.dedup_key):
            return IncidentResult(incident_id=None, created=False, reason="duplicate_open_dedup")
        raise

    return IncidentResult(incident_id=incident.id, created=True, reason="created")
