from pathlib import Path

import pytest

from edge.vision.zone_config import CameraConfig, load_all_camera_configs

CONFIG_DIR = Path(__file__).resolve().parents[1] / "config" / "cameras"


def test_load_all_returns_all_cameras():
    cfgs = load_all_camera_configs(CONFIG_DIR)
    assert len(cfgs) >= 4


def test_load_all_returns_camera_configs():
    cfgs = load_all_camera_configs(CONFIG_DIR)
    for cfg in cfgs:
        assert isinstance(cfg, CameraConfig)


def test_load_all_distinct_camera_ids():
    cfgs = load_all_camera_configs(CONFIG_DIR)
    ids = [cfg.camera_id for cfg in cfgs]
    assert len(ids) == len(set(ids)), "camera_ids must all be distinct"


def test_load_all_each_has_valid_zones():
    cfgs = load_all_camera_configs(CONFIG_DIR)
    for cfg in cfgs:
        assert len(cfg.zones) >= 1
        for zone in cfg.zones:
            assert len(zone.polygon) >= 3


def test_load_all_raises_on_empty_dir(tmp_path):
    with pytest.raises(FileNotFoundError, match="no cam_\\*.yaml"):
        load_all_camera_configs(tmp_path)
