from __future__ import annotations

import pytest

from cloud.common.ws.contract import WsType
from cloud.common.ws.manager import ConnectionManager


class FakeWS:
    """Minimal duck-typed stand-in for starlette WebSocket.send_json."""

    def __init__(self, *, fail: bool = False) -> None:
        self.sent: list[dict] = []
        self.fail = fail
        self.accepted = False

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, data: dict) -> None:
        if self.fail:
            raise RuntimeError("socket closed")
        self.sent.append(data)


@pytest.mark.asyncio
async def test_connect_registers_and_accepts():
    mgr = ConnectionManager()
    ws = FakeWS()
    conn = await mgr.connect(ws)
    assert ws.accepted is True
    assert mgr.connection_count == 1
    assert conn.seq == 0


@pytest.mark.asyncio
async def test_seq_is_monotonic_per_connection():
    mgr = ConnectionManager()
    ws = FakeWS()
    conn = await mgr.connect(ws)
    await mgr.send(conn, WsType.SYSTEM_HEARTBEAT, {})
    await mgr.send(conn, WsType.SYSTEM_HEARTBEAT, {})
    await mgr.send(conn, WsType.SYSTEM_HEARTBEAT, {})
    seqs = [m["seq"] for m in ws.sent]
    assert seqs == [1, 2, 3]
    assert all(m["version"] == 1 for m in ws.sent)
    assert all("server_now" in m for m in ws.sent)


@pytest.mark.asyncio
async def test_each_connection_has_independent_seq():
    mgr = ConnectionManager()
    a, b = FakeWS(), FakeWS()
    ca = await mgr.connect(a)
    await mgr.connect(b)
    await mgr.send(ca, WsType.SYSTEM_HEARTBEAT, {})
    await mgr.broadcast(WsType.INCIDENT_UPDATED, {"incident_id": "x"})
    # a got seq 1 (direct) then 2 (broadcast); b got only seq 1 (broadcast)
    assert [m["seq"] for m in a.sent] == [1, 2]
    assert [m["seq"] for m in b.sent] == [1]


@pytest.mark.asyncio
async def test_broadcast_returns_count_and_reaches_all():
    mgr = ConnectionManager()
    a, b = FakeWS(), FakeWS()
    await mgr.connect(a)
    await mgr.connect(b)
    n = await mgr.broadcast(WsType.INCIDENT_CREATED, {"incident_id": "z"})
    assert n == 2
    assert a.sent[0]["type"] == "incident.created"
    assert b.sent[0]["data"] == {"incident_id": "z"}


@pytest.mark.asyncio
async def test_broadcast_drops_failed_connection_and_continues():
    mgr = ConnectionManager()
    good, bad = FakeWS(), FakeWS(fail=True)
    await mgr.connect(good)
    await mgr.connect(bad)
    n = await mgr.broadcast(WsType.INCIDENT_UPDATED, {"incident_id": "q"})
    assert n == 1                      # only the good one counted
    assert mgr.connection_count == 1   # bad one was evicted
    assert good.sent[0]["data"] == {"incident_id": "q"}


@pytest.mark.asyncio
async def test_subscribe_records_topics_and_last_seq():
    mgr = ConnectionManager()
    ws = FakeWS()
    conn = await mgr.connect(ws)
    mgr.subscribe(conn, ["incidents", "timers"], last_seq=42)
    assert conn.subscriptions == {"incidents", "timers"}
    assert conn.client_last_seq == 42


@pytest.mark.asyncio
async def test_disconnect_removes_connection():
    mgr = ConnectionManager()
    ws = FakeWS()
    conn = await mgr.connect(ws)
    mgr.disconnect(conn)
    assert mgr.connection_count == 0
    # idempotent
    mgr.disconnect(conn)
    assert mgr.connection_count == 0
