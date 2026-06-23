import inspect

from cloud.tools.consume_print import consume_one
from cloud.tools.publish_fixture import load_fixture_event, main as publish_main
from cloud.common.schemas.anomaly import AnomalyEvent


def test_fixture_loader_returns_valid_event() -> None:
    ev = load_fixture_event()
    assert isinstance(ev, AnomalyEvent)
    assert ev.camera_id == "cam_01"
    assert ev.anomaly_type.value == "ppe_no_hardhat"


def test_tool_entrypoints_are_coroutines() -> None:
    assert inspect.iscoroutinefunction(publish_main)
    assert inspect.iscoroutinefunction(consume_one)


def test_makefile_has_checkpoint_targets() -> None:
    from pathlib import Path

    makefile = Path(__file__).resolve().parents[2] / "Makefile"
    text = makefile.read_text(encoding="utf-8")
    assert "publish-fixture:" in text
    assert "consume-print:" in text
