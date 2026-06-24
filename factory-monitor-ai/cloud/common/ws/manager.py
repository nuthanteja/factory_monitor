"""In-process WebSocket connection registry + fan-out (design §5.5).

Each Connection owns its own monotonic seq so a client can detect a forward
gap and REST-resync. broadcast() is the API the Redis fan-out (slice ws-redis)
calls; it is resilient to a dead socket (drops it, keeps fanning out).
"""

from __future__ import annotations

from typing import Protocol

from cloud.common.ws.contract import WsType, make_envelope


class _SendableWS(Protocol):
    async def accept(self) -> None: ...

    async def send_json(self, data: dict) -> None: ...


class Connection:
    __slots__ = ("ws", "seq", "subscriptions", "client_last_seq")

    def __init__(self, ws: _SendableWS) -> None:
        self.ws = ws
        self.seq = 0  # last seq assigned to THIS connection
        self.subscriptions: set[str] = set()
        self.client_last_seq: int = 0

    def next_seq(self) -> int:
        self.seq += 1
        return self.seq


class ConnectionManager:
    def __init__(self) -> None:
        self._conns: set[Connection] = set()
        # single-threaded asyncio: set mutations are atomic w.r.t. the event loop, no lock needed

    @property
    def connection_count(self) -> int:
        return len(self._conns)

    async def connect(self, ws: _SendableWS) -> Connection:
        await ws.accept()
        conn = Connection(ws)
        self._conns.add(conn)
        return conn

    def disconnect(self, conn: Connection) -> None:
        self._conns.discard(conn)  # idempotent

    def subscribe(
        self, conn: Connection, topics: list[str], last_seq: int
    ) -> None:
        conn.subscriptions = set(topics)
        conn.client_last_seq = last_seq

    async def send(self, conn: Connection, type: WsType, data: dict) -> None:
        """Send a message to a single connection.

        Caller is responsible for catching exceptions and disconnecting on failure
        (unlike broadcast, which auto-drops failed connections).
        """
        env = make_envelope(type, seq=conn.next_seq(), data=data)
        await conn.ws.send_json(env)

    async def broadcast(self, type: WsType, data: dict) -> int:
        """Fan-out a message to all connected clients.

        Returns count of successful sends. Drops any connection that fails framing or send.
        """
        sent = 0
        dead: list[Connection] = []
        for conn in list(self._conns):
            try:
                env = make_envelope(type, seq=conn.next_seq(), data=data)
                await conn.ws.send_json(env)
                sent += 1
            except Exception:
                dead.append(conn)
        for conn in dead:
            self.disconnect(conn)
        return sent
