from __future__ import annotations

import uuid

import pytest

from cloud.common.ws_events import CHANGE_CREATED, decode_change, incident_change
from cloud.common.ws_publisher import publish_incident_event


class _RecordingRedis:
    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []

    async def publish(self, channel: str, message: str) -> int:
        self.published.append((channel, message))
        return 1


class _BoomRedis:
    async def publish(self, channel: str, message: str) -> int:
        raise ConnectionError("redis is down")


@pytest.mark.asyncio
async def test_publish_issues_encoded_change_on_channel():
    redis = _RecordingRedis()
    inc_id = uuid.uuid4()
    change = incident_change(CHANGE_CREATED, inc_id)

    ok = await publish_incident_event(redis, "dashboard:incidents", change)

    assert ok is True
    assert len(redis.published) == 1
    chan, msg = redis.published[0]
    assert chan == "dashboard:incidents"
    assert decode_change(msg) == change


@pytest.mark.asyncio
async def test_publish_with_none_client_is_noop_returns_false():
    inc_id = uuid.uuid4()
    change = incident_change(CHANGE_CREATED, inc_id)
    ok = await publish_incident_event(None, "dashboard:incidents", change)
    assert ok is False  # nothing to publish to; must not raise


@pytest.mark.asyncio
async def test_publish_swallows_redis_errors_and_returns_false(caplog):
    inc_id = uuid.uuid4()
    change = incident_change(CHANGE_CREATED, inc_id)
    # A Redis outage must NEVER propagate into the caller's transaction path.
    ok = await publish_incident_event(_BoomRedis(), "dashboard:incidents", change)
    assert ok is False
    assert any("publish" in r.message.lower() for r in caplog.records)
