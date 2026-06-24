"""Best-effort Redis publisher for live incident-change fan-out (design §3.2).

publish_incident_event is the ONLY way the writers (ingest consumer,
escalation transition, ack/resolve service) notify the live UI. It is
intentionally best-effort: it runs AFTER the DB txn has committed and must
NEVER raise — a Redis outage degrades the dashboard to the Postgres-poll
fallback (design §8 "Redis down") but must not fail escalation/ingest/ack.
"""
from __future__ import annotations

import logging

from cloud.common.ws_events import encode_change

logger = logging.getLogger(__name__)


async def publish_incident_event(
    redis_client: object | None,
    channel: str,
    change: dict,
) -> bool:
    """Publish a compact change event; return True iff issued. Never raises.

    redis_client is anything exposing `async publish(channel, message)`
    (e.g. redis.asyncio.Redis). None => no-op (Redis not configured/connected).
    """
    if redis_client is None:
        logger.debug("ws publish skipped (no redis client) change=%s", change.get("change_type"))
        return False
    try:
        await redis_client.publish(channel, encode_change(change))
        return True
    except Exception:  # noqa: BLE001 — best-effort; outage must not break the writer
        logger.warning(
            "ws publish failed (redis fan-out degraded to poll fallback) change=%s",
            change.get("change_type"),
            exc_info=True,
        )
        return False
