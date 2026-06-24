from __future__ import annotations

from datetime import UTC, datetime

from cloud.common.ws.contract import IncidentView, WsType, make_envelope


def test_wstype_phase2b_subset_values():
    # Exact wire strings from design §5.5 — the frontend depends on these.
    assert WsType.SNAPSHOT.value == "snapshot"
    assert WsType.INCIDENT_CREATED.value == "incident.created"
    assert WsType.INCIDENT_UPDATED.value == "incident.updated"
    assert WsType.INCIDENT_TIER_ADVANCED.value == "incident.tier_advanced"
    assert WsType.INCIDENT_RESOLVED.value == "incident.resolved"
    assert WsType.TIMER_SNAPSHOT.value == "timer.snapshot"
    assert WsType.SYSTEM_HEARTBEAT.value == "system.heartbeat"
    # Phase-4 types are deliberately absent in the 2b subset.
    assert {t.value for t in WsType} == {
        "snapshot", "incident.created", "incident.updated",
        "incident.tier_advanced", "incident.resolved",
        "timer.snapshot", "system.heartbeat",
    }


def test_make_envelope_shape_and_serialization():
    now = datetime(2026, 6, 23, 10, 5, 3, tzinfo=UTC)
    env = make_envelope(WsType.SYSTEM_HEARTBEAT, seq=7, data={}, server_now=now)
    assert env["type"] == "system.heartbeat"
    assert env["version"] == 1
    assert env["seq"] == 7
    assert env["data"] == {}
    # server_now is ISO-8601 UTC text, round-trippable.
    assert datetime.fromisoformat(env["server_now"]) == now
    # server_now MUST have the Z suffix (design §5.5).
    assert env["server_now"].endswith("Z")


def test_make_envelope_defaults_server_now_to_now():
    before = datetime.now(tz=UTC)
    env = make_envelope(WsType.SNAPSHOT, seq=1, data={"incidents": []})
    parsed = datetime.fromisoformat(env["server_now"])
    assert parsed >= before


def test_incident_view_field_set_is_locked():
    iv = IncidentView(
        incident_id="11111111-1111-4111-8111-111111111111",
        camera_id="cam_01",
        zone_id="zone_weld_bay",
        rule_id="PPE_NO_HARDHAT",
        anomaly_type="ppe_no_hardhat",
        severity="high",
        object_class="person",
        status="AWAITING_OPERATOR",
        current_tier=0,
        deadline_at=datetime(2026, 6, 23, 10, 7, 0, tzinfo=UTC),
        opened_at=datetime(2026, 6, 23, 10, 5, 0, tzinfo=UTC),
        snapshot_url=None,
        tier_label="Operator",
    )
    dumped = iv.model_dump(mode="json")
    assert set(dumped.keys()) == {
        "incident_id", "camera_id", "zone_id", "rule_id", "anomaly_type",
        "severity", "object_class", "status", "current_tier",
        "deadline_at", "opened_at", "snapshot_url", "tier_label",
    }
    assert dumped["deadline_at"] == "2026-06-23T10:07:00Z"
    assert dumped["incident_id"] == "11111111-1111-4111-8111-111111111111"


def test_incident_view_terminal_deadline_null():
    iv = IncidentView(
        incident_id="x", camera_id="cam_01", zone_id=None, rule_id="PPE_NO_HARDHAT",
        anomaly_type="ppe_no_hardhat", severity="high", object_class="person",
        status="CRITICAL_UNRESOLVED", current_tier=3, deadline_at=None,
        opened_at=datetime(2026, 6, 23, 10, 5, 0, tzinfo=UTC),
        snapshot_url=None, tier_label="CRITICAL",
    )
    assert iv.model_dump(mode="json")["deadline_at"] is None


def test_incident_view_model_dump_always_iso_z():
    """IncidentView.model_dump() (no mode arg) must always emit ISO-Z strings.

    This is critical: the WS broadcaster calls model_dump() without mode="json",
    so datetimes must serialize to JSON-safe strings in both python and json modes.
    """
    iv = IncidentView(
        incident_id="11111111-1111-4111-8111-111111111111",
        camera_id="cam_01",
        zone_id="zone_weld_bay",
        rule_id="PPE_NO_HARDHAT",
        anomaly_type="ppe_no_hardhat",
        severity="high",
        object_class="person",
        status="AWAITING_OPERATOR",
        current_tier=0,
        deadline_at=datetime(2026, 6, 23, 10, 7, 0, tzinfo=UTC),
        opened_at=datetime(2026, 6, 23, 10, 5, 0, tzinfo=UTC),
        snapshot_url=None,
        tier_label="Operator",
    )
    # model_dump() with NO mode arg should still yield ISO-Z strings.
    dumped = iv.model_dump()
    assert dumped["opened_at"] == "2026-06-23T10:05:00Z"
    assert dumped["deadline_at"] == "2026-06-23T10:07:00Z"
    # Also verify mode="json" still works.
    dumped_json = iv.model_dump(mode="json")
    assert dumped_json["opened_at"] == "2026-06-23T10:05:00Z"
    assert dumped_json["deadline_at"] == "2026-06-23T10:07:00Z"
    # And terminal deadline still passes None through.
    iv_terminal = IncidentView(
        incident_id="x", camera_id="cam_01", zone_id=None, rule_id="PPE_NO_HARDHAT",
        anomaly_type="ppe_no_hardhat", severity="high", object_class="person",
        status="CRITICAL_UNRESOLVED", current_tier=3, deadline_at=None,
        opened_at=datetime(2026, 6, 23, 10, 5, 0, tzinfo=UTC),
        snapshot_url=None, tier_label="CRITICAL",
    )
    assert iv_terminal.model_dump()["deadline_at"] is None
