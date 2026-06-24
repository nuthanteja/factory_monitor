"""Guard the README documents the Phase-2b live WebSocket layer.

Structural text check (no Docker, no network) — mirrors the repo's other
presence checks (test_compose_*_wiring, test_repo_layout).
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # factory-monitor-ai/


def _readme() -> str:
    return (ROOT / "README.md").read_text(encoding="utf-8")


def test_readme_has_ws_section() -> None:
    text = _readme()
    assert "/ws/live" in text, "README must document the /ws/live endpoint"


def test_readme_describes_fanout_and_fallback() -> None:
    text = _readme().lower()
    assert "redis" in text and "pub/sub" in text, "README must describe Redis pub/sub fan-out"
    assert "fallback" in text and "poll" in text, (
        "README must describe the Postgres-poll fallback channel"
    )


def test_readme_describes_server_authoritative_timers() -> None:
    text = _readme().lower()
    assert "server-authoritative" in text or "server_now" in text, (
        "README must describe the server-authoritative timer model"
    )
    assert "deadline_at" in text, "README must mention absolute deadline_at"
