import numpy as np

from edge.vision.detector import (
    Detection,
    PpeDetector,
    _attach_ppe,
)


class _FakeBox:
    def __init__(self, xyxy, conf, cls):
        self.xyxy = [np.array(xyxy, dtype=float)]
        self.conf = [float(conf)]
        self.cls = [float(cls)]


class _FakeResult:
    def __init__(self, names, rows):
        self.names = names
        self.boxes = [_FakeBox(*r) for r in rows]


class _FakeModel:
    def __init__(self, names, rows):
        self._names = names
        self._rows = rows

    def __call__(self, frame, verbose=False, conf=0.0):
        return [_FakeResult(self._names, self._rows)]


NAMES = {0: "person", 1: "hardhat", 2: "no-hardhat"}


def test_attach_ppe_marks_person_without_hardhat():
    person = Detection("person", (100, 100, 200, 400), 0.9, no_hardhat=False)
    no_hardhat_box = (150, 110, 60, 60)
    out = _attach_ppe([person], [no_hardhat_box])
    assert len(out) == 1
    assert out[0].no_hardhat is True


def test_attach_ppe_leaves_person_with_hardhat_clean():
    person = Detection("person", (100, 100, 200, 400), 0.9, no_hardhat=False)
    out = _attach_ppe([person], [(900, 900, 40, 40)])
    assert out[0].no_hardhat is False


def test_detect_returns_person_with_no_hardhat():
    rows = [
        ((100, 100, 300, 500), 0.92, 0),
        ((150, 110, 210, 170), 0.80, 2),
    ]
    model = _FakeModel(NAMES, rows)
    det = PpeDetector(model, person_conf=0.35, ppe_conf=0.35)
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    out = det.detect(frame)
    persons = [d for d in out if d.object_class == "person"]
    assert len(persons) == 1
    assert persons[0].no_hardhat is True
    assert persons[0].bbox == (100, 100, 200, 400)


def test_detect_filters_low_confidence_person():
    rows = [((100, 100, 300, 500), 0.10, 0)]
    model = _FakeModel(NAMES, rows)
    det = PpeDetector(model, person_conf=0.35, ppe_conf=0.35)
    out = det.detect(np.zeros((720, 1280, 3), dtype=np.uint8))
    assert out == []


def test_detect_person_with_hardhat_is_not_flagged():
    rows = [
        ((100, 100, 300, 500), 0.92, 0),
        ((150, 110, 210, 170), 0.80, 1),
    ]
    model = _FakeModel(NAMES, rows)
    det = PpeDetector(model, person_conf=0.35, ppe_conf=0.35)
    out = det.detect(np.zeros((720, 1280, 3), dtype=np.uint8))
    persons = [d for d in out if d.object_class == "person"]
    assert len(persons) == 1
    assert persons[0].no_hardhat is False
