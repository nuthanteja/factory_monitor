from edge.vision.detector import Detection
from edge.vision.roboflow_detector import map_detections


def test_head_maps_to_no_hardhat_person():
    out = map_detections([("head", (10, 20, 30, 40), 0.8)])
    assert out == [Detection("person", (10, 20, 30, 40), 0.8, no_hardhat=True)]


def test_helmet_maps_to_compliant_person():
    out = map_detections([("helmet", (1, 2, 3, 4), 0.9)])
    assert out == [Detection("person", (1, 2, 3, 4), 0.9, no_hardhat=False)]


def test_label_match_is_case_insensitive():
    out = map_detections([("HEAD", (5, 5, 5, 5), 0.7)])
    assert out == [Detection("person", (5, 5, 5, 5), 0.7, no_hardhat=True)]


def test_unknown_labels_are_ignored():
    out = map_detections([("forklift", (0, 0, 1, 1), 0.5), ("dog", (2, 2, 2, 2), 0.6)])
    assert out == []


def test_mixed_scene_maps_each_worker_and_drops_noise():
    preds = [
        ("helmet", (100, 100, 50, 50), 0.86),
        ("head", (200, 120, 48, 48), 0.81),
        ("banana", (0, 0, 9, 9), 0.99),
    ]
    out = map_detections(preds)
    assert len(out) == 2
    assert [d.no_hardhat for d in out] == [False, True]
    assert all(d.object_class == "person" for d in out)
