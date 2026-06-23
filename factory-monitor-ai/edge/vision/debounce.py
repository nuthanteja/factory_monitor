from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Literal

from shapely.geometry import Point, Polygon


def point_in_polygon(
    point: tuple[float, float], polygon: list[tuple[int, int]]
) -> bool:
    """True if point is inside or on the boundary of polygon."""
    poly = Polygon(polygon)
    return bool(poly.covers(Point(point)))


@dataclass(frozen=True)
class DebounceConfig:
    window: int = 12
    m_of_n: int = 8
    clear_consecutive: int = 6


@dataclass(frozen=True)
class DebounceEvent:
    track_id: str
    rule_id: str
    transition: Literal["open", "clear"]


@dataclass
class _TrackState:
    history: deque[bool]
    is_open: bool = False
    clear_streak: int = 0


@dataclass
class TrackDebouncer:
    """M-of-N debounce per (track_id, rule_id).

    observe() is called once per frame per (track, rule). It returns:
      * DebounceEvent('open')  exactly once when >= m_of_n of the last
        `window` frames are violating and the track is not already open;
      * DebounceEvent('clear') exactly once after `clear_consecutive`
        consecutive non-violating frames while open;
      * None otherwise.
    """

    config: DebounceConfig = field(default_factory=DebounceConfig)
    _states: dict[tuple[str, str], _TrackState] = field(default_factory=dict)

    def observe(
        self, track_id: str, rule_id: str, violating: bool
    ) -> DebounceEvent | None:
        key = (track_id, rule_id)
        st = self._states.get(key)
        if st is None:
            st = _TrackState(history=deque(maxlen=self.config.window))
            self._states[key] = st

        st.history.append(violating)

        if violating:
            st.clear_streak = 0
        else:
            st.clear_streak += 1

        if not st.is_open:
            if sum(st.history) >= self.config.m_of_n:
                st.is_open = True
                st.clear_streak = 0
                return DebounceEvent(track_id, rule_id, "open")
            return None

        if st.clear_streak >= self.config.clear_consecutive:
            st.is_open = False
            st.clear_streak = 0
            st.history.clear()
            return DebounceEvent(track_id, rule_id, "clear")
        return None
