from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, computed_field, field_validator

from cloud.common.ws.incident_view import tier_label_for


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
    object_class: str | None
    deadline_at: datetime | None

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

    @computed_field  # type: ignore[prop-decorator]
    @property
    def tier_label(self) -> str:
        return tier_label_for(self.status, self.current_tier)


class IncidentListResponse(BaseModel):
    incidents: list[IncidentOut]
    meta: dict[str, str]
