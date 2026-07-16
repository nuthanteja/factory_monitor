"""Adapt a Roboflow hard-hat model to the edge ``Detector`` contract.

The Roboflow ``hard-hat-detect`` model emits two classes — ``helmet`` (a hard hat)
and ``head`` (a bare head) — and **no** ``person`` box. So unlike the original
``PpeDetector`` (which finds person boxes and attaches PPE boxes to them), here
each detection already *is* one worker:

  * a ``head`` box  -> a person with ``no_hardhat=True``  (a violation candidate)
  * a ``helmet`` box -> a compliant person (``no_hardhat=False``)

Everything downstream — zone membership, ByteTrack, the M-of-N debounce, and the
``AnomalyEvent`` schema — is unchanged, because this returns the same
``Detection`` objects the engine already consumes.

The label mapping lives in the pure ``map_detections`` function so it is unit
testable without the CV stack; ``supervision`` is imported lazily inside
``detect`` only when the model actually runs.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import numpy as np

from edge.vision.detector import Detection

# Label vocabulary of the hard-hat-detect model (compared case-insensitively).
HELMET_LABELS = {"helmet", "hardhat", "hard-hat"}
NO_HARDHAT_LABELS = {"head", "no-hardhat", "no_hardhat"}

# One parsed box: (label, (x, y, w, h) pixels, confidence).
Prediction = tuple[str, tuple[int, int, int, int], float]


def map_detections(predictions: Iterable[Prediction]) -> list[Detection]:
    """Map hard-hat-detect boxes to edge ``Detection`` objects.

    The model has no ``person`` class, so each box already *is* one worker: a
    ``head`` box becomes a person with ``no_hardhat=True``, a ``helmet`` box a
    compliant person. Unknown labels are ignored. Pure and supervision-free so
    the mapping is unit-testable without the CV stack.
    """
    out: list[Detection] = []
    for label, bbox, conf in predictions:
        norm = str(label).lower()
        if norm in NO_HARDHAT_LABELS:
            out.append(Detection("person", bbox, conf, no_hardhat=True))
        elif norm in HELMET_LABELS:
            out.append(Detection("person", bbox, conf, no_hardhat=False))
    return out


class RoboflowPpeDetector:
    """Wraps a loaded Roboflow model (anything exposing ``.infer``)."""

    def __init__(self, model: Any, conf: float = 0.4, iou: float = 0.5) -> None:
        self.model = model
        self.conf = conf
        self.iou = iou

    def detect(self, frame: np.ndarray) -> list[Detection]:
        import supervision as sv

        results = self.model.infer(
            frame, confidence=self.conf, iou_threshold=self.iou
        )[0]
        dets = sv.Detections.from_inference(results)
        names = list(dets.data.get("class_name", []))

        preds: list[Prediction] = []
        for i in range(len(dets)):
            x1, y1, x2, y2 = (float(v) for v in dets.xyxy[i])
            bbox = (
                int(round(x1)),
                int(round(y1)),
                int(round(x2 - x1)),
                int(round(y2 - y1)),
            )
            conf = float(dets.confidence[i]) if dets.confidence is not None else 0.0
            label = names[i] if i < len(names) else ""
            preds.append((label, bbox, conf))
        return map_detections(preds)
