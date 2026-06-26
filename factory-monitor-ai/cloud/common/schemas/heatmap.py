from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class HeatmapCell(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    zone_id: str
    count: int
    ts: datetime


class CameraHeatmap(BaseModel):
    camera_id: str
    cells: list[HeatmapCell]


class HeatmapResponse(BaseModel):
    cameras: list[CameraHeatmap]
    meta: dict


class ZoneOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    camera_id: str | None
    name: str | None
    polygon: list  # list of [x, y] passed through from JSONB


class ZoneListResponse(BaseModel):
    zones: list[ZoneOut]
