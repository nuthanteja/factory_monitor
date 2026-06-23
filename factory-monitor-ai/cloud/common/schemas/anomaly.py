"""Canonical AnomalyEvent contract — the producer payload on vision.anomalies.v1.

Single source of truth imported by both the cloud ingest worker and the edge
publisher.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AnomalyType(str, Enum):
    PPE_NO_HARDHAT = "ppe_no_hardhat"
    PPE_NO_VEST = "ppe_no_vest"
    ZONE_INTRUSION = "zone_intrusion"
    LOITERING = "loitering"
    FORKLIFT_IN_PEDESTRIAN_ZONE = "forklift_in_pedestrian_zone"
    DUTY_ZONE_ABSENCE = "duty_zone_absence"
    DENSITY_THRESHOLD = "density_threshold"


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bbox: list[int] = Field(..., min_length=4, max_length=4)
    snapshot_url: str = ""
    footage_source: str = ""


class AnomalyEvent(BaseModel):
    """The JSON message produced to vision.anomalies.v1 (key = camera_id)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    event_id: str
    anomaly_type: AnomalyType
    rule_id: str
    occurred_at: datetime
    site_id: str
    camera_id: str
    zone_id: str | None = None
    track_id: str
    object_class: Literal["person", "forklift"]
    severity: Severity
    confidence: float = Field(..., ge=0.0, le=1.0)
    dedup_key: str
    evidence: Evidence
    source: Literal["edge", "replay"] = "edge"
