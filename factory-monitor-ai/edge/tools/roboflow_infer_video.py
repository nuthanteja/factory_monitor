#!/usr/bin/env python3
"""Run Roboflow ``hard-hat-detect/1`` LOCALLY over a video and write an annotated copy.

For each processed frame it runs local inference (weights cached on first use),
draws the boxes, and writes an annotated output video so you can eyeball how well
the model detects workers + hard hats on YOUR footage. It also aggregates the
class labels across the clip and prints them at the end — those exact strings are
what get mapped into the edge PPE contract (``edge/vision/detector.py``).

Inference runs on CPU by default and is the slow part, so by default it only runs
the model every ``stride`` frames and reuses the last boxes in between (the output
is still full-length and smooth). Increase ``stride`` if it's too slow; keep test
clips short (10-30s).

The API key is read from the environment ONLY. Rotate it if it was ever pasted
anywhere; never commit it.

Setup (dedicated venv — NOT the cloud .venv):
    py -3.11 -m venv .venv-cv
    .\\.venv-cv\\Scripts\\python.exe -m pip install --upgrade pip
    .\\.venv-cv\\Scripts\\python.exe -m pip install inference supervision opencv-python

Usage (args: input.mp4 [output.mp4] [conf] [stride]):
    # PowerShell:  $env:ROBOFLOW_API_KEY = "your-rotated-key"
    .\\.venv-cv\\Scripts\\python.exe edge\\tools\\roboflow_infer_video.py workers.mp4
"""
from __future__ import annotations

import collections
import os
import sys
import time

MODEL_ID = "hard-hat-detect/1"


def _open_writer(path: str, fps: float, size: tuple[int, int]):
    import cv2

    writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), fps, size)
    if not writer.isOpened():  # some Windows builds lack the mp4v encoder
        alt = os.path.splitext(path)[0] + ".avi"
        writer = cv2.VideoWriter(alt, cv2.VideoWriter_fourcc(*"XVID"), fps, size)
        if writer.isOpened():
            print(f"note: mp4v unavailable, writing {alt} instead", file=sys.stderr)
            return writer, alt
    return writer, path


def main() -> int:
    if len(sys.argv) < 2:
        print(
            "usage: python edge/tools/roboflow_infer_video.py "
            "<input.mp4> [output.mp4] [conf] [stride]",
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

    in_path = sys.argv[1]
    out_path = sys.argv[2] if len(sys.argv) > 2 else "annotated.mp4"
    conf = float(sys.argv[3]) if len(sys.argv) > 3 else 0.5
    stride = max(1, int(sys.argv[4])) if len(sys.argv) > 4 else 5

    import cv2
    import supervision as sv
    from inference import get_model

    cap = cv2.VideoCapture(in_path)
    if not cap.isOpened():
        print(f"ERROR: could not open video: {in_path}", file=sys.stderr)
        return 2

    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
    print(f"input={in_path}  {width}x{height}@{fps:.1f}fps  frames~{total}")
    print(f"model={MODEL_ID}  conf>={conf}  stride={stride}  -> {out_path}")

    writer, out_path = _open_writer(out_path, fps, (width, height))
    model = get_model(model_id=MODEL_ID, api_key=api_key)

    box_ann_cls = getattr(sv, "BoxAnnotator", None) or sv.BoundingBoxAnnotator
    box_ann = box_ann_cls()
    label_ann = sv.LabelAnnotator()

    counts: collections.Counter[str] = collections.Counter()
    last = sv.Detections.empty()
    idx = 0
    processed = 0
    t0 = time.time()

    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if idx % stride == 0:
            results = model.infer(frame, confidence=conf, iou_threshold=0.5)[0]
            last = sv.Detections.from_inference(results)
            counts.update(last.data.get("class_name", []))
            processed += 1
            if processed % 20 == 0:
                print(f"  processed {processed} inferences ({idx} frames)...")

        annotated = box_ann.annotate(scene=frame.copy(), detections=last)
        try:
            names = list(last.data.get("class_name", []))
            labels = [
                f"{c} {p:.2f}"
                for c, p in zip(names, last.confidence, strict=False)
            ]
            annotated = label_ann.annotate(
                scene=annotated, detections=last, labels=labels
            )
        except Exception:
            pass
        writer.write(annotated)
        idx += 1

    cap.release()
    writer.release()

    dt = time.time() - t0
    print(f"\ndone: {idx} frames written to {out_path} in {dt:.1f}s "
          f"({processed} model inferences)")
    print("class counts across the clip:")
    for cls, n in counts.most_common():
        print(f"  {cls!r}: {n}")
    print("\n--- distinct class labels (map these into edge/vision/detector.py) ---")
    print(sorted(c for c in counts if c is not None))
    return 0


if __name__ == "__main__":
    sys.exit(main())
