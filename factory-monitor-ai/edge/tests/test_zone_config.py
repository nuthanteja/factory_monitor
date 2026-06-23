from pathlib import Path

import pytest

from edge.vision.zone_config import (
    CameraConfig,
    ZoneConfig,
    load_camera_config,
)

CONFIG_PATH = (
    Path(__file__).resolve().parents[1] / "config" / "cameras" / "cam_01.yaml"
)


def test_loads_cam_01_config():
    cfg = load_camera_config(CONFIG_PATH)
    assert isinstance(cfg, CameraConfig)
    assert cfg.camera_id == "cam_01"
    assert cfg.site_id == "plant-01"
    assert cfg.rtsp_url == "rtsp://mediamtx:8554/cam_01"
    assert len(cfg.zones) == 1


def test_zone_is_required_ppe_polygon():
    cfg = load_camera_config(CONFIG_PATH)
    zone = cfg.zones[0]
    assert isinstance(zone, ZoneConfig)
    assert zone.zone_id == "zone_weld_bay"
    assert zone.kind == "required_ppe"
    assert zone.polygon == [(200, 120), (1100, 120), (1100, 700), (200, 700)]
    assert len(zone.polygon) >= 3
    assert all(isinstance(p, tuple) and len(p) == 2 for p in zone.polygon)


def test_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        load_camera_config("/no/such/cam.yaml")


def test_rejects_unknown_zone_kind(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "camera_id: c\nsite_id: s\nrtsp_url: rtsp://x\n"
        "zones:\n  - zone_id: z\n    kind: bogus_kind\n"
        "    polygon: [[0,0],[1,0],[1,1]]\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_camera_config(bad)
