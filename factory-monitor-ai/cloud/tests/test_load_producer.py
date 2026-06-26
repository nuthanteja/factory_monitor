"""Validate that load/producer/anomaly_load.py::build_event() produces a payload
that satisfies the real AnomalyEvent schema (extra="forbid").

These tests always run — no docker, no Kafka, no network required.
"""
from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

# Allow importing the producer without installing it as a package.
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "load" / "producer"))

from anomaly_load import build_event  # noqa: E402

from cloud.common.schemas.anomaly import AnomalyEvent  # noqa: E402


def test_build_event_validates_against_real_schema() -> None:
    """build_event() must produce an AnomalyEvent that round-trips through
    AnomalyEvent.model_validate (extra='forbid') without raising."""
    event = build_event()
    # Proves the payload satisfies extra="forbid" — no extra fields allowed.
    validated = AnomalyEvent.model_validate(event.model_dump())
    assert validated.event_id == event.event_id


def test_occurred_at_is_approximately_now() -> None:
    """occurred_at must be set to the current UTC time, not a static timestamp.

    A static timestamp makes the ingest latency histogram meaningless (it would
    always report a huge number).  We allow a 5-second window to account for
    test execution overhead.
    """
    event = build_event()
    delta = (datetime.now(tz=UTC) - event.occurred_at).total_seconds()
    assert 0 <= delta < 5, (
        f"occurred_at is {delta:.3f}s from now — expected < 5s"
    )


def test_unique_event_id_and_dedup_key_per_call() -> None:
    """Each call to build_event() must produce distinct event_id AND dedup_key.

    Reusing event_id causes the ingest worker to silently dedup events.
    Reusing dedup_key causes the second event to be dropped as a duplicate of
    an open incident — the load is swallowed and the SLO test is meaningless.
    """
    e1 = build_event()
    e2 = build_event()
    assert e1.event_id != e2.event_id, "event_id must be unique per call"
    assert e1.dedup_key != e2.dedup_key, "dedup_key must be unique per call"


def test_source_is_replay() -> None:
    """The load producer must use source='replay', not 'edge'."""
    event = build_event()
    assert event.source == "replay"


def test_camera_id_is_valid() -> None:
    """camera_id must be one of the seeded cam_01..cam_06."""
    valid = {f"cam_{i:02d}" for i in range(1, 7)}
    event = build_event()
    assert event.camera_id in valid, (
        f"camera_id '{event.camera_id}' not in {valid}"
    )


def test_confidence_in_range() -> None:
    """confidence must be in [0.0, 1.0]."""
    event = build_event()
    assert 0.0 <= event.confidence <= 1.0


def test_evidence_bbox_has_four_ints() -> None:
    """Evidence.bbox must have exactly 4 integers."""
    event = build_event()
    assert len(event.evidence.bbox) == 4
    assert all(isinstance(v, int) for v in event.evidence.bbox)
