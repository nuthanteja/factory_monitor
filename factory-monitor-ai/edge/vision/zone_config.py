from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, field_validator


class ZoneConfig(BaseModel):
    zone_id: str
    kind: Literal["required_ppe"]
    polygon: list[tuple[int, int]]

    @field_validator("polygon")
    @classmethod
    def _at_least_triangle(
        cls, v: list[tuple[int, int]]
    ) -> list[tuple[int, int]]:
        if len(v) < 3:
            raise ValueError("polygon must have at least 3 vertices")
        return v


class CameraConfig(BaseModel):
    camera_id: str
    site_id: str
    rtsp_url: str
    zones: list[ZoneConfig]


def load_camera_config(path: str | Path) -> CameraConfig:
    """Load and validate a camera YAML config into a CameraConfig."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"camera config not found: {p}")
    with p.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    return CameraConfig.model_validate(raw)
