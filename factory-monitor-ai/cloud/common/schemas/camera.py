from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class CameraOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str | None
    whep_url: str | None
    zone_id: str | None
    rtsp_path: str


class CameraListResponse(BaseModel):
    cameras: list[CameraOut]
