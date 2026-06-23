"""Download YOLOv8 PPE weights into edge/models/.

Phase 1 default: a base YOLOv8n is fetched by ultralytics on first use, but a
PPE-specific model is preferred. Set PPE_WEIGHTS_URL to a hosted .pt to fetch a
hardhat/no-hardhat fine-tune; otherwise we fall back to yolov8n.pt (person-only,
detector still runs; no-hardhat boxes simply won't appear until a PPE model is
supplied). The engine and all tests are robust to either.
"""
from __future__ import annotations

import os
import sys
import urllib.request
from pathlib import Path

MODELS_DIR = Path(__file__).resolve().parent
PPE_WEIGHTS = MODELS_DIR / "ppe_yolov8.pt"
BASE_WEIGHTS = MODELS_DIR / "yolov8n.pt"
BASE_URL = (
    "https://github.com/ultralytics/assets/releases/download/v8.3.0/yolov8n.pt"
)


def _download(url: str, dest: Path) -> None:
    print(f"[weights] downloading {url} -> {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(url, dest)
    print(f"[weights] done ({dest.stat().st_size} bytes)")


def main() -> int:
    ppe_url = os.environ.get("PPE_WEIGHTS_URL", "")
    if ppe_url:
        _download(ppe_url, PPE_WEIGHTS)
        return 0
    if not BASE_WEIGHTS.exists():
        _download(BASE_URL, BASE_WEIGHTS)
    print(
        "[weights] NOTE: no PPE_WEIGHTS_URL set; using base yolov8n.pt "
        "(person detection only). Set PPE_WEIGHTS_URL for hardhat detection."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
