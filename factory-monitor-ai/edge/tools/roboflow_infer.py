#!/usr/bin/env python3
"""Sanity-check the Roboflow ``hard-hat-detect/1`` model and discover its labels.

Runs ONE inference through Roboflow's hosted serverless API and prints the
distinct class names it returns. We need those exact labels to map the model
into the edge detector's PPE contract (see ``edge/vision/detector.py``:
``PERSON_LABELS`` / ``NO_HARDHAT_LABELS``) before wiring it into the pipeline.

The API key is read from the environment ONLY — never hardcode it, never commit
it. If you pasted it anywhere, rotate it in Roboflow first.

Usage:
    # PowerShell:  $env:ROBOFLOW_API_KEY = "your-rotated-key"
    # bash:        export ROBOFLOW_API_KEY=your-rotated-key
    python edge/tools/roboflow_infer.py path\\to\\image.jpg
    python edge/tools/roboflow_infer.py https://example.com/worker.jpg

Install (hosted path only — no torch, tiny):
    pip install inference-sdk
"""
from __future__ import annotations

import collections
import json
import os
import sys

MODEL_ID = "hard-hat-detect/1"
API_URL = "https://serverless.roboflow.com"


def main() -> int:
    if len(sys.argv) != 2:
        print(
            "usage: python edge/tools/roboflow_infer.py <image-path-or-url>",
            file=sys.stderr,
        )
        return 2

    api_key = os.environ.get("ROBOFLOW_API_KEY")
    if not api_key:
        print(
            "ERROR: set ROBOFLOW_API_KEY in your environment (do NOT hardcode it).\n"
            '  PowerShell:  $env:ROBOFLOW_API_KEY = "your-rotated-key"\n'
            "  bash:        export ROBOFLOW_API_KEY=your-rotated-key",
            file=sys.stderr,
        )
        return 2

    image = sys.argv[1]

    from inference_sdk import InferenceHTTPClient

    client = InferenceHTTPClient(api_url=API_URL, api_key=api_key)
    result = client.infer(image, model_id=MODEL_ID)

    preds = result.get("predictions", []) if isinstance(result, dict) else []
    classes = collections.Counter(p.get("class") for p in preds)

    print(f"model={MODEL_ID}  image={image}")
    print(f"predictions: {len(preds)}")
    print("class counts:")
    for cls, n in classes.most_common():
        print(f"  {cls!r}: {n}")
    print("\n--- distinct class labels (these get mapped into the edge contract) ---")
    print(sorted(c for c in classes if c is not None))
    print("\n--- raw JSON ---")
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
