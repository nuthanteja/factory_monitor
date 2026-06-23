from cloud.common.db.base import Base
from cloud.common.db import models  # noqa: F401  (registers tables on Base)
from cloud.common.db.models import IncidentStatus

EXPECTED_TABLES = {
    "incidents",
    "incident_events",
    "escalation_idempotency",
    "outbox",
    "messages",
    "whatsapp_sessions",
    "unmatched_inbound",
    "users",
    "on_call_assignments",
    "escalation_tiers",
    "zones",
    "cameras",
    "density_snapshots",
}


def test_all_tables_registered() -> None:
    assert EXPECTED_TABLES <= set(Base.metadata.tables.keys())


def test_incident_status_enum_members() -> None:
    assert IncidentStatus.AWAITING_OPERATOR.value == "AWAITING_OPERATOR"
    assert {m.value for m in IncidentStatus} >= {
        "AWAITING_OPERATOR", "TIER1", "TIER2", "ACK", "RESOLVED", "CRITICAL_UNRESOLVED"
    }


def test_incidents_key_columns() -> None:
    cols = Base.metadata.tables["incidents"].columns
    for name in (
        "id", "site_id", "camera_id", "zone_id", "anomaly_type", "rule_id",
        "object_class", "track_id", "severity", "dedup_key", "status",
        "current_tier", "next_fire_at", "deadline_at", "snapshot_url",
        "is_synthetic", "created_at", "updated_at",
    ):
        assert name in cols, f"incidents missing column {name}"
    assert cols["current_tier"].default.arg == 0


def test_incident_events_key_columns() -> None:
    cols = Base.metadata.tables["incident_events"].columns
    for name in ("id", "incident_id", "type", "from_state", "to_state",
                 "tier", "source_event_id", "payload", "created_at"):
        assert name in cols, f"incident_events missing column {name}"


def test_incident_events_fk_to_incidents() -> None:
    fks = Base.metadata.tables["incident_events"].foreign_keys
    targets = {fk.column.table.name for fk in fks}
    assert "incidents" in targets
