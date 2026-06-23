from edge.vision.debounce import (
    DebounceConfig,
    DebounceEvent,
    TrackDebouncer,
    point_in_polygon,
)

SQUARE = [(0, 0), (10, 0), (10, 10), (0, 10)]


def test_point_inside_polygon():
    assert point_in_polygon((5, 5), SQUARE) is True


def test_point_outside_polygon():
    assert point_in_polygon((50, 50), SQUARE) is False


def test_point_on_edge_counts_as_inside():
    assert point_in_polygon((0, 5), SQUARE) is True


def _feed(deb, track, rule, seq):
    out = []
    for v in seq:
        ev = deb.observe(track, rule, v)
        if ev is not None:
            out.append(ev.transition)
    return out


def test_emits_open_once_when_m_of_n_confirmed():
    deb = TrackDebouncer(DebounceConfig(window=12, m_of_n=8, clear_consecutive=6))
    out = _feed(deb, "cam_01:1", "PPE_NO_HARDHAT", [True] * 8)
    assert out == ["open"]


def test_does_not_open_below_threshold():
    deb = TrackDebouncer(DebounceConfig(window=12, m_of_n=8, clear_consecutive=6))
    out = _feed(deb, "cam_01:1", "PPE_NO_HARDHAT", [True] * 7 + [False] * 5)
    assert out == []


def test_open_then_clear_after_k_consecutive_clear():
    deb = TrackDebouncer(DebounceConfig(window=12, m_of_n=8, clear_consecutive=6))
    assert _feed(deb, "cam_01:1", "PPE_NO_HARDHAT", [True] * 8) == ["open"]
    assert _feed(deb, "cam_01:1", "PPE_NO_HARDHAT", [False] * 5) == []
    assert _feed(deb, "cam_01:1", "PPE_NO_HARDHAT", [False]) == ["clear"]


def test_clear_counter_resets_on_violation():
    deb = TrackDebouncer(DebounceConfig(window=12, m_of_n=8, clear_consecutive=6))
    _feed(deb, "cam_01:1", "PPE_NO_HARDHAT", [True] * 8)
    assert _feed(deb, "cam_01:1", "PPE_NO_HARDHAT", [False] * 5 + [True]) == []
    assert _feed(deb, "cam_01:1", "PPE_NO_HARDHAT", [False] * 5) == []
    assert _feed(deb, "cam_01:1", "PPE_NO_HARDHAT", [False]) == ["clear"]


def test_no_duplicate_open_while_already_open():
    deb = TrackDebouncer(DebounceConfig(window=12, m_of_n=8, clear_consecutive=6))
    assert _feed(deb, "cam_01:1", "PPE_NO_HARDHAT", [True] * 20) == ["open"]


def test_tracks_are_independent():
    deb = TrackDebouncer(DebounceConfig(window=12, m_of_n=8, clear_consecutive=6))
    assert _feed(deb, "cam_01:1", "PPE_NO_HARDHAT", [True] * 8) == ["open"]
    assert _feed(deb, "cam_01:2", "PPE_NO_HARDHAT", [True] * 8) == ["open"]


def test_debounce_event_shape():
    deb = TrackDebouncer(DebounceConfig(window=12, m_of_n=8, clear_consecutive=6))
    ev = None
    for _ in range(8):
        ev = deb.observe("cam_01:9", "PPE_NO_HARDHAT", True)
    assert isinstance(ev, DebounceEvent)
    assert ev.track_id == "cam_01:9"
    assert ev.rule_id == "PPE_NO_HARDHAT"
    assert ev.transition == "open"


def test_rule_id_independence():
    """Same track_id with different rule_ids maintain independent state."""
    deb = TrackDebouncer(DebounceConfig(window=12, m_of_n=8, clear_consecutive=6))
    # Feed 8× PPE_NO_HARDHAT violations on cam_01:1
    out1 = _feed(deb, "cam_01:1", "PPE_NO_HARDHAT", [True] * 8)
    assert out1 == ["open"]
    # Feed 8× PPE_NO_VEST violations on same cam_01:1
    # Should also emit "open" because this is a different rule_id state
    out2 = _feed(deb, "cam_01:1", "PPE_NO_VEST", [True] * 8)
    assert out2 == ["open"]


def test_open_clear_reopen_cycle():
    """Verify full transition cycle: open -> clear -> open."""
    deb = TrackDebouncer(DebounceConfig(window=12, m_of_n=8, clear_consecutive=6))
    out = []
    # Feed 8× violations (expect "open")
    out.extend(_feed(deb, "cam_01:1", "PPE_NO_HARDHAT", [True] * 8))
    # Feed 6× clears (expect "clear")
    out.extend(_feed(deb, "cam_01:1", "PPE_NO_HARDHAT", [False] * 6))
    # Feed 8× violations again (expect "open")
    out.extend(_feed(deb, "cam_01:1", "PPE_NO_HARDHAT", [True] * 8))
    assert out == ["open", "clear", "open"]
