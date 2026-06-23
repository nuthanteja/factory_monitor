from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator


class IncidentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    camera_id: str
    zone_id: str | None
    anomaly_type: str
    rule_id: str
    severity: str
    status: str
    current_tier: int
    created_at: datetime
    snapshot_url: str | None

    @field_validator("status", "anomaly_type", "severity", mode="before")
    @classmethod
    def _enum_to_str(cls, v: object) -> object:
        return v.value if hasattr(v, "value") else v

    @field_validator("snapshot_url", mode="before")
    @classmethod
    def _empty_str_to_none(cls, v: object) -> object:
        if v == "":
            return None
        return v


class IncidentListResponse(BaseModel):
    incidents: list[IncidentOut]
    meta: dict[str, str]
