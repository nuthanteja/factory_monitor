"""Project an Incident ORM row into the WS IncidentView (design §5.5)."""
from __future__ import annotations

from cloud.common.db.models import Incident
from cloud.common.ws.contract import IncidentView

# current_tier (0/1/2/3) → human label. CRITICAL_UNRESOLVED short-circuits below.
_TIER_LABELS = {0: "Operator", 1: "Floor Manager", 2: "Plant Director", 3: "CRITICAL"}


def tier_label_for(status: str, current_tier: int) -> str:
    if status == "CRITICAL_UNRESOLVED":
        return "CRITICAL"
    return _TIER_LABELS.get(current_tier, "Operator")


def build_incident_view(inc: Incident) -> IncidentView:
    status = inc.status.value if hasattr(inc.status, "value") else str(inc.status)
    return IncidentView(
        incident_id=str(inc.id),
        camera_id=inc.camera_id,
        zone_id=inc.zone_id,
        rule_id=inc.rule_id,
        anomaly_type=inc.anomaly_type,
        severity=inc.severity,
        object_class=inc.object_class,
        status=status,
        current_tier=inc.current_tier,
        deadline_at=inc.deadline_at,
        opened_at=inc.created_at,
        snapshot_url=inc.snapshot_url,
        tier_label=tier_label_for(status, inc.current_tier),
    )
