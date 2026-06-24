from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from cloud.common.ws.contract import WsType
from cloud.common.ws_events import (
    CHANGE_CREATED,
    CHANGE_RESOLVED,
    CHANGE_TIER_ADVANCED,
    CHANGE_UPDATED,
    decode_change,
    encode_change,
    incident_change,
)


def test_change_type_constants_match_locked_contract():
    assert CHANGE_CREATED == WsType.INCIDENT_CREATED.value
    assert CHANGE_UPDATED == WsType.INCIDENT_UPDATED.value
    assert CHANGE_TIER_ADVANCED == WsType.INCIDENT_TIER_ADVANCED.value
    assert CHANGE_RESOLVED == WsType.INCIDENT_RESOLVED.value


def test_incident_change_is_compact_and_stringifies_uuid():
    inc_id = uuid.uuid4()
    change = incident_change(CHANGE_CREATED, inc_id)
    assert change == {"change_type": "incident.created", "incident_id": str(inc_id)}
    # No full view fields leak in by default — it must stay compact.
    assert set(change.keys()) == {"change_type", "incident_id"}


def test_incident_change_carries_optional_minimal_fields():
    inc_id = uuid.uuid4()
    change = incident_change(
        CHANGE_TIER_ADVANCED, inc_id, current_tier=1, status="TIER1"
    )
    assert change["current_tier"] == 1
    assert change["status"] == "TIER1"


def test_encode_decode_roundtrip_handles_uuid_and_datetime():
    inc_id = uuid.uuid4()
    dl = datetime(2026, 6, 23, 10, 0, 0, tzinfo=UTC)
    change = incident_change(
        CHANGE_TIER_ADVANCED, inc_id, current_tier=2, deadline_at=dl
    )
    raw = encode_change(change)
    assert isinstance(raw, str)
    back = decode_change(raw)
    assert back["change_type"] == "incident.tier_advanced"
    assert back["incident_id"] == str(inc_id)
    assert back["current_tier"] == 2
    # datetime serialised as ISO-8601 string
    assert back["deadline_at"] == dl.isoformat()
    # decode also accepts bytes (redis returns bytes)
    assert decode_change(raw.encode("utf-8")) == back
    # and it's valid JSON
    assert json.loads(raw)["incident_id"] == str(inc_id)
