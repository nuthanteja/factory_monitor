from __future__ import annotations

from typing import Iterator, Protocol

import numpy as np


class FrameSource(Protocol):
    def frames(self) -> Iterator[np.ndarray]:
        """Yield BGR frames as numpy arrays until exhausted/stopped."""
        ...


class StubFrameSource:
    """Yields a fixed, in-memory list of frames (for tests)."""

    def __init__(self, frames: list[np.ndarray]):
        self._frames = frames

    def frames(self) -> Iterator[np.ndarray]:
        yield from self._frames


class RtspFrameSource:
    """Pulls frames from an RTSP URL via OpenCV (cv2.VideoCapture)."""

    def __init__(self, rtsp_url: str):
        self._url = rtsp_url

    def frames(self) -> Iterator[np.ndarray]:
        import cv2  # local import: keep tests import-light

        cap = cv2.VideoCapture(self._url, cv2.CAP_FFMPEG)
        if not cap.isOpened():
            raise RuntimeError(f"cannot open RTSP stream: {self._url}")
        try:
            while True:
                ok, frame = cap.read()
                if not ok:
                    break
                yield frame
        finally:
            cap.release()
