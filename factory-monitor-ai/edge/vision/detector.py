from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

import numpy as np

ObjectClass = Literal["person", "forklift"]

PERSON_LABELS = {"person"}
NO_HARDHAT_LABELS = {"no-hardhat", "no_hardhat", "head"}
HARDHAT_LABELS = {"hardhat", "helmet"}  # Unused: no-hardhat is determined by containment check
FORKLIFT_LABELS = {"forklift"}


@dataclass(frozen=True)
class Detection:
    object_class: ObjectClass
    bbox: tuple[int, int, int, int]  # (x, y, w, h) image pixels
    confidence: float
    no_hardhat: bool


class _YoloLike(Protocol):
    names: dict[int, str]

    def __call__(self, frame: np.ndarray, verbose: bool = ..., conf: float = ...): ...


def _xyxy_to_xywh(xyxy) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = (int(round(float(v))) for v in xyxy[:4])
    return (x1, y1, x2 - x1, y2 - y1)


def _contains(person_bbox, box_bbox) -> bool:
    """True if the center of box_bbox lies inside person_bbox (x,y,w,h)."""
    px, py, pw, ph = person_bbox
    bx, by, bw, bh = box_bbox
    cx, cy = bx + bw / 2.0, by + bh / 2.0
    return px <= cx <= px + pw and py <= cy <= py + ph


def _attach_ppe(
    persons: list[Detection],
    no_hardhat_boxes: list[tuple[int, int, int, int]],
) -> list[Detection]:
    """Mark a person as no_hardhat if a no-hardhat box is contained in them."""
    out: list[Detection] = []
    for p in persons:
        flagged = any(_contains(p.bbox, nh) for nh in no_hardhat_boxes)
        out.append(
            Detection(
                object_class=p.object_class,
                bbox=p.bbox,
                confidence=p.confidence,
                no_hardhat=flagged,
            )
        )
    return out


class PpeDetector:
    def __init__(
        self,
        model: _YoloLike,
        person_conf: float = 0.35,
        ppe_conf: float = 0.35,
    ) -> None:
        self.model = model
        self.person_conf = person_conf
        self.ppe_conf = ppe_conf

    def detect(self, frame: np.ndarray) -> list[Detection]:
        results = self.model(frame, verbose=False, conf=0.0)
        result = results[0]
        names = result.names

        persons: list[Detection] = []
        no_hardhat_boxes: list[tuple[int, int, int, int]] = []
        forklifts: list[Detection] = []

        for box in result.boxes:
            cls_id = int(float(box.cls[0]))
            conf = float(box.conf[0])
            label = names[cls_id]
            xywh = _xyxy_to_xywh(box.xyxy[0])

            if label in PERSON_LABELS:
                if conf >= self.person_conf:
                    persons.append(
                        Detection("person", xywh, conf, no_hardhat=False)
                    )
            elif label in NO_HARDHAT_LABELS:
                if conf >= self.ppe_conf:
                    no_hardhat_boxes.append(xywh)
            elif label in FORKLIFT_LABELS:
                if conf >= self.person_conf:
                    forklifts.append(
                        Detection("forklift", xywh, conf, no_hardhat=False)
                    )

        return _attach_ppe(persons, no_hardhat_boxes) + forklifts
