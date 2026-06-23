import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from cloud.common.schemas.anomaly import AnomalyEvent, AnomalyType, Severity

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = REPO_ROOT / "shared" / "contracts" / "anomaly_event.example.json"


def _fixture_dict() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_fixture_validates_against_model() -> None:
    ev = AnomalyEvent.model_validate(_fixture_dict())
    assert ev.schema_version == "1.0"
    assert ev.anomaly_type is AnomalyType.PPE_NO_HARDHAT
    assert ev.camera_id == "cam_01"
    assert ev.severity is Severity.HIGH
    assert ev.evidence.bbox == [880, 412, 130, 348]
    assert ev.evidence.footage_source == "clip_03"
    assert ev.source == "edge"


def test_round_trip_preserves_canonical_fields() -> None:
    raw = _fixture_dict()
    ev = AnomalyEvent.model_validate(raw)
    reserialized = ev.model_dump(mode="json")
    again = AnomalyEvent.model_validate(reserialized)
    assert again == ev
    assert reserialized["occurred_at"].endswith("Z") or "+00:00" in reserialized["occurred_at"]


def test_unknown_anomaly_type_rejected() -> None:
    bad = _fixture_dict()
    bad["anomaly_type"] = "definitely_not_a_type"
    with pytest.raises(ValueError):
        AnomalyEvent.model_validate(bad)


def test_zone_id_nullable() -> None:
    d = _fixture_dict()
    d["zone_id"] = None
    ev = AnomalyEvent.model_validate(d)
    assert ev.zone_id is None


def test_object_class_and_source_constraints() -> None:
    """Ensure object_class and source are constrained to Literal values."""
    valid = _fixture_dict()

    # Test invalid object_class
    invalid_object_class = valid.copy()
    invalid_object_class["object_class"] = "robot"
    with pytest.raises(ValidationError):
        AnomalyEvent.model_validate(invalid_object_class)

    # Test invalid source
    invalid_source = valid.copy()
    invalid_source["source"] = "Replay"
    with pytest.raises(ValidationError):
        AnomalyEvent.model_validate(invalid_source)
