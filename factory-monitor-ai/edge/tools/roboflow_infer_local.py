#!/usr/bin/env python3
"""Run Roboflow ``hard-hat-detect/1`` LOCALLY on one image and report its labels.

Uses Roboflow's ``inference`` package (``get_model``), which downloads and caches
the model weights on the first run, then runs on-box with no per-frame network
call. This is the same local-inference path the edge pipeline will use, so it
doubles as a weights-download + sanity check.

The most important output is the "distinct class labels" line — those exact
strings get mapped into the edge PPE contract (``edge/vision/detector.py``:
``PERSON_LABELS`` / ``NO_HARDHAT_LABELS``).

The API key is read from the environment ONLY. Rotate it if it has ever been
pasted anywhere, and never commit it.

Setup (use a DEDICATED venv, not the cloud .venv):
    py -3.11 -m venv .venv-cv
    .\\.venv-cv\\Scripts\\python.exe -m pip install --upgrade pip
    .\\.venv-cv\\Scripts\\python.exe -m pip install inference supervision opencv-python

Usage (args: image.jpg [output.jpg] [conf]):
    # PowerShell:  $env:ROBOFLOW_API_KEY = "your-rotated-key"
    .\\.venv-cv\\Scripts\\python.exe edge\\tools\\roboflow_infer_local.py image.jpg
"""
from __future__ import annotations

import collections
import os
import sys

MODEL_ID = "hard-hat-detect/1"


def main() -> int:
    if len(sys.argv) < 2:
        print(
            "usage: python edge/tools/roboflow_infer_local.py "
            "<image.jpg> [output.jpg] [conf]",
            file=sys.stderr,
        )
        return 2

    api_key = os.environ.get("ROBOFLOW_API_KEY")
    if not api_key:
        print(
            "ERROR: set ROBOFLOW_API_KEY in your environment (do NOT hardcode it).\n"
            '  PowerShell:  $env:ROBOFLOW_API_KEY = "your-rotated-key"',
            file=sys.stderr,
        )
        return 2

    image_path = sys.argv[1]
    out_path = sys.argv[2] if len(sys.argv) > 2 else "output.jpg"
    conf = float(sys.argv[3]) if len(sys.argv) > 3 else 0.5

    import cv2
    import supervision as sv
    from inference import get_model

    image = cv2.imread(image_path)
    if image is None:
        print(f"ERROR: could not read image: {image_path}", file=sys.stderr)
        return 2

    # Downloads + caches the weights on first run; local thereafter.
    model = get_model(model_id=MODEL_ID, api_key=api_key)
    results = model.infer(image, confidence=conf, iou_threshold=0.5)[0]
    detections = sv.Detections.from_inference(results)

    names = list(detections.data.get("class_name", []))
    counts = collections.Counter(names)
    print(f"model={MODEL_ID}  image={image_path}  conf>={conf}")
    print(f"detections: {len(detections)}")
    for cls, n in counts.most_common():
        print(f"  {cls!r}: {n}")
    print("\n--- distinct class labels (map these into edge/vision/detector.py) ---")
    print(sorted(set(names)))

    # Annotate + save (robust across supervision versions; non-fatal if it fails).
    try:
        box_ann = getattr(sv, "BoxAnnotator", None) or sv.BoundingBoxAnnotator
        annotated = box_ann().annotate(scene=image.copy(), detections=detections)
        try:
            labels = [
                f"{c} {p:.2f}"
                for c, p in zip(names, detections.confidence, strict=False)
            ]
            annotated = sv.LabelAnnotator().annotate(
                scene=annotated, detections=detections, labels=labels
            )
        except Exception:
            pass
        cv2.imwrite(out_path, annotated)
        print(f"\nsaved annotated image -> {out_path}")
    except Exception as exc:  # annotation is a nicety, not the point
        print(f"\n(annotation skipped: {exc})")

    return 0


if __name__ == "__main__":
    sys.exit(main())
