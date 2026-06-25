"""Pure-file wiring assertions for Phase 4a: MediaMTX WHEP + same-origin /whep proxy.

No Docker required — all checks are structural reads of config files.

Asserts:
- footage/mediamtx.yml enables WebRTC (webrtc: yes / true), sets webrtcAddress,
  defines 6 cam_0N paths.
- compose.yaml publishes 8189:8189/udp and pins the mediamtx image (not :latest).
- frontend/nginx.conf has a location /whep/ block with resolver 127.0.0.11 and
  a variable proxy_pass referencing mediamtx on port 8889.
- frontend/vite.config.ts has a /whep proxy entry referencing mediamtx:8889.
"""
from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]  # factory-monitor-ai/


def _mediamtx() -> dict:
    return yaml.safe_load((ROOT / "footage" / "mediamtx.yml").read_text(encoding="utf-8"))


def _compose() -> dict:
    return yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))


def _nginx() -> str:
    return (ROOT / "frontend" / "nginx.conf").read_text(encoding="utf-8")


def _vite() -> str:
    return (ROOT / "frontend" / "vite.config.ts").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# footage/mediamtx.yml — WebRTC enabled + 6 camera paths
# ---------------------------------------------------------------------------


def test_mediamtx_webrtc_enabled() -> None:
    cfg = _mediamtx()
    val = cfg.get("webrtc")
    assert val is True or val == "yes", (
        f"footage/mediamtx.yml must have 'webrtc: yes', got: {val!r}"
    )


def test_mediamtx_webrtc_address() -> None:
    cfg = _mediamtx()
    addr = cfg.get("webrtcAddress")
    assert addr is not None, "footage/mediamtx.yml must declare 'webrtcAddress'"
    assert "8889" in str(addr), (
        f"webrtcAddress must reference port 8889, got: {addr!r}"
    )


def test_mediamtx_six_camera_paths() -> None:
    cfg = _mediamtx()
    paths = cfg.get("paths", {})
    for n in range(1, 7):
        key = f"cam_{n:02d}"
        assert key in paths, (
            f"footage/mediamtx.yml must define path '{key}'; found: {list(paths.keys())}"
        )


# ---------------------------------------------------------------------------
# compose.yaml — 8189/udp published, mediamtx image pinned (not :latest)
# ---------------------------------------------------------------------------


def test_compose_mediamtx_publishes_udp_8189() -> None:
    svc = _compose()["services"]["mediamtx"]
    ports = [str(p) for p in svc.get("ports", [])]
    assert any("8189" in p and "udp" in p for p in ports), (
        f"mediamtx service must publish '8189:8189/udp' for WebRTC media; "
        f"got ports: {ports}"
    )


def test_compose_mediamtx_image_pinned() -> None:
    svc = _compose()["services"]["mediamtx"]
    image = svc.get("image", "")
    assert ":latest" not in image, (
        f"mediamtx image must be pinned to a specific tag (not :latest), got: {image!r}"
    )
    assert "bluenviron/mediamtx" in image, (
        f"mediamtx image must start with 'bluenviron/mediamtx', got: {image!r}"
    )


# ---------------------------------------------------------------------------
# frontend/nginx.conf — /whep/ location with resolver + variable proxy_pass
# ---------------------------------------------------------------------------


def test_nginx_whep_location_block() -> None:
    nginx = _nginx()
    assert "location /whep/" in nginx, (
        "frontend/nginx.conf must contain 'location /whep/' block"
    )


def test_nginx_whep_resolver() -> None:
    nginx = _nginx()
    assert "resolver 127.0.0.11" in nginx, (
        "frontend/nginx.conf /whep/ block must use 'resolver 127.0.0.11' "
        "so nginx boots without MediaMTX"
    )


def test_nginx_whep_variable_proxy_pass_mediamtx_8889() -> None:
    nginx = _nginx()
    # The variable ($mediamtx) defers DNS; proxy_pass must reference mediamtx on 8889.
    assert "$mediamtx" in nginx, (
        "frontend/nginx.conf /whep/ block must use a variable upstream ($mediamtx) "
        "to defer DNS resolution"
    )
    assert "8889" in nginx, (
        "frontend/nginx.conf /whep/ proxy_pass must reference port 8889"
    )


# ---------------------------------------------------------------------------
# frontend/vite.config.ts — /whep proxy entry → mediamtx:8889
# ---------------------------------------------------------------------------


def test_vite_whep_proxy_entry() -> None:
    vite = _vite()
    assert '"/whep"' in vite or "'/whep'" in vite, (
        "frontend/vite.config.ts must have a '/whep' proxy entry"
    )


def test_vite_whep_proxy_targets_mediamtx_8889() -> None:
    vite = _vite()
    assert "mediamtx:8889" in vite, (
        "frontend/vite.config.ts /whep proxy must target 'mediamtx:8889'"
    )
